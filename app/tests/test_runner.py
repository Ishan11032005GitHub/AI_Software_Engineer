import subprocess, os, tempfile, json
from typing import NamedTuple

class TestResult(NamedTuple):
    success: bool
    output: str

def run_tests(repo_path: str) -> TestResult:
    """
    Runs pytest inside repo. Returns success + output.
    """
    try:
        # If repo has no tests, treat as pass
        if not os.path.exists(os.path.join(repo_path, "tests")):
            return TestResult(True, "No tests folder — treated as pass")

        cmd = ["pytest", "-q", "--disable-warnings", "--maxfail=1"]
        proc = subprocess.run(
            cmd,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=120
        )

        success = proc.returncode == 0
        out = proc.stdout + "\n" + proc.stderr
        return TestResult(success, out)

    except Exception as e:
        return TestResult(False, f"Test runner crashed: {e}")
