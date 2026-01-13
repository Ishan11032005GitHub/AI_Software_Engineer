import time
from app.ci.actions_client import get_failed_logs_best_effort
from app.ci.test_failure_parser import parse_ci_logs

def wait_for_check_and_fetch(owner, repo, sha, token, timeout=1800, poll=45):
    """
    Polls GitHub Actions until completion or timeout.
    Returns CI text logs if failed; None if success or timeout.
    """
    start = time.time()

    while time.time() - start < timeout:
        bundle = get_failed_logs_best_effort(owner, repo, token, preferred_head_sha=sha)

        if not bundle or not bundle.run:
            time.sleep(poll)
            continue

        status = bundle.run.conclusion
        print(f"⏳ CI status: {status}")

        if status == "success":
            return None
        
        if status in {"failure", "timed_out", "cancelled"}:
            return bundle.text

        time.sleep(poll)

    return "timeout"
