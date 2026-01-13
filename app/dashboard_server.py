# app/dashboard_server.py
from __future__ import annotations

import sqlite3
import time
from functools import wraps
from threading import Thread

from flask import (
    Flask, render_template, request,
    redirect, url_for, session,
    flash, jsonify
)

from app.storage.artifact_store import ArtifactStore
from app.agents.agent_runner import run_agent_pipeline
from config import SQLITE_PATH

# ----------------- Flask app -----------------

app = Flask(__name__)
app.secret_key = "dev-secret-change-me"

store = ArtifactStore(SQLITE_PATH)
store.init_db()

AGENT_ACTIONS = [
    ("fix_bugs", "Fix Bugs"),
    ("refactor", "Refactor Code"),
    ("add_feature", "Add Feature"),
    ("generate_project", "Generate Project"),
    ("run_tests", "Run Tests"),
    ("create_pr", "Create PR"),
]

AGENT_ACTION_KEYS = {a[0] for a in AGENT_ACTIONS}

# ----------------- Auth -----------------

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        p = request.form.get("password", "").strip()

        if not u or not p:
            flash("Missing credentials", "error")
            return render_template("auth/signup.html")

        try:
            store.create_user(u, p)
        except sqlite3.IntegrityError:
            flash("User exists", "error")
            return render_template("auth/signup.html")

        flash("Signup successful", "success")
        return redirect(url_for("login"))

    return render_template("auth/signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        p = request.form.get("password", "").strip()

        if store.validate_user(u, p):
            session["user_id"] = u
            return redirect(url_for("dashboard"))

        flash("Invalid credentials", "error")

    return render_template("auth/login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ----------------- Dashboard -----------------

@app.route("/")
def root():
    return redirect("/dashboard" if "user_id" in session else "/login")


@app.route("/dashboard")
@login_required
def dashboard():
    return render_template(
        "dashboard/index.html",
        stats=store.get_dashboard_stats(),
        jobs=store.get_jobs(20),
    )

# ----------------- Sessions -----------------

@app.route("/sessions")
@login_required
def sessions_list():
    return render_template(
        "dashboard/sessions.html",
        sessions=store.get_sessions(session["user_id"]),
    )


@app.route("/sessions/new", methods=["POST"])
@login_required
def create_session():
    sid = store.create_session(
        session["user_id"],
        request.form.get("name") or "AI Session",
    )
    return redirect(url_for("session_view", sid=sid))


@app.route("/session/<int:sid>")
@login_required
def session_view(sid):
    return render_template(
        "dashboard/session.html",
        session_obj=store.get_session(sid),
        repos=store.get_repos_for_session(sid),
        jobs=store.get_agent_jobs_for_session(sid),
        actions=AGENT_ACTIONS,
    )


@app.route("/session/<int:sid>/attach_repo", methods=["POST"])
@login_required
def attach_repo(sid):
    raw = request.form.get("repo", "").strip()
    if not raw:
        flash("Repository URL required", "error")
        return redirect(url_for("session_view", sid=sid))

    owner, repo = raw.replace("https://github.com/", "").split("/", 1)
    local_path = f"./repos/{owner}__{repo}"

    store.attach_repo(sid, owner, repo, local_path)
    flash(f"Attached {owner}/{repo}", "success")
    return redirect(url_for("session_view", sid=sid))


@app.route("/session/<int:sid>/run", methods=["POST"])
@login_required
def run_session(sid):
    repos = store.get_repos_for_session(sid)
    if not repos:
        flash("Attach a repository first", "error")
        return redirect(url_for("session_view", sid=sid))

    repo_id = request.form.get("repo_id")
    repo = next((r for r in repos if str(r["id"]) == str(repo_id)), None)
    if not repo:
        flash("Invalid repo", "error")
        return redirect(url_for("session_view", sid=sid))

    action = (request.form.get("action") or "").strip()
    prompt = (request.form.get("prompt") or "").strip()

    if action not in AGENT_ACTION_KEYS:
        flash(f"Invalid action: {action}", "error")
        return redirect(url_for("session_view", sid=sid))

    job_id = store.enqueue_agent_job(
        session_id=sid,
        owner=repo["owner"],
        repo=repo["repo"],
        action=action,
        prompt=prompt,
    )

    store.append_job_event(
        job_id,
        "SERVER_ENQUEUED",
        f"{repo['owner']}/{repo['repo']} :: {action}",
    )

    flash(f"Job #{job_id} queued ({action})", "success")
    return redirect(url_for("session_view", sid=sid))

# ----------------- Job Inspect -----------------

@app.route("/dashboard/jobs/<int:job_id>")
@login_required
def job_detail(job_id):
    job = store.get_job(job_id)
    if not job:
        return "Job not found", 404

    return render_template(
        "dashboard/job_detail.html",
        job=job,
        events=store.get_job_events(job_id),
    )

# ----------------- Job Actions -----------------

@app.route("/api/jobs/<int:job_id>/action", methods=["POST"])
@login_required
def job_action(job_id):
    data = request.json or {}
    action = data.get("action")

    if action == "abort":
        store.update_agent_job_status(job_id, "ABORTED")
    elif action == "approve":
        store.append_job_event(job_id, "APPROVED", "{}")
    elif action == "retry":
        job = store.get_job(job_id)
        store.enqueue_agent_job(
            job["session_id"],
            job["owner"],
            job["repo"],
            job["action"],
            job["prompt"],
        )
    else:
        return jsonify({"error": "invalid action"}), 400

    return jsonify({"ok": True})

# ----------------- Worker -----------------

def job_worker():
    while True:
        job = store.fetch_next_agent_job()
        if not job:
            time.sleep(1)
            continue

        jid = job["id"]

        try:
            store.update_agent_job_status(jid, "RUNNING")

            run_agent_pipeline(
                owner=job["owner"],
                repo=job["repo"],
                action=job["action"],
                prompt=job["prompt"],
                job_id=jid,
                store=store,
            )

            if store.get_job(jid)["status"] == "RUNNING":
                store.update_agent_job_status(jid, "COMPLETED")

        except Exception as e:
            store.append_job_event(jid, "ERROR", str(e))
            store.update_agent_job_status(jid, "FAILED")


@app.before_request
def start_worker():
    if not hasattr(app, "_worker_started"):
        Thread(target=job_worker, daemon=True).start()
        app._worker_started = True


if __name__ == "__main__":
    app.run(port=8000, debug=True)
