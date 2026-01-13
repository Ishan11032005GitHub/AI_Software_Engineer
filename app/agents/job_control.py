import time
import json

class JobControl:
    def __init__(self, store, job_id: int, poll_sec: float = 1.0):
        self.store = store
        self.job_id = job_id
        self.poll_sec = poll_sec

    def status(self) -> str:
        return (self.store.get_job(self.job_id) or {}).get("status") or ""

    def should_abort(self) -> bool:
        return self.status() in ("ABORTED", "CANCELLED")

    def wait_if_paused(self):
        while True:
            st = self.status()
            if st != "PAUSED":
                return
            time.sleep(self.poll_sec)

    def block_with_question(self, question: str, context: dict | None = None):
        payload = {"question": question, "context": context or {}}
        self.store.append_job_event(self.job_id, "QUESTION", json.dumps(payload))
        self.store.update_job_status(self.job_id, "BLOCKED")

    def wait_for_user_input(self) -> dict:
        # waits until a USER_INPUT event appears after the latest QUESTION
        while True:
            if self.should_abort():
                raise RuntimeError("Job aborted/cancelled while blocked.")

            self.wait_if_paused()

            evt = self.store.get_latest_job_event(self.job_id, "USER_INPUT")
            if evt:
                try:
                    return json.loads(evt["payload_json"] or "{}")
                except Exception:
                    return {"raw": evt["payload_json"]}

            time.sleep(self.poll_sec)

    def log(self, msg: str):
        self.store.append_job_event(self.job_id, "LOG", msg)
