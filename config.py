import os
from dotenv import load_dotenv
load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

BASE_BRANCH = os.getenv("BASE_BRANCH","main")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SQLITE_PATH = os.getenv(
    "SQLITE_PATH",
    os.path.join(BASE_DIR, "data", "agent.sqlite")
)

MAX_CHANGED_LINES = int(os.getenv("MAX_CHANGED_LINES","25"))
GITHUB_API="https://api.github.com"

PR_DRAFT_THRESHOLD=0.10
PR_FULL_THRESHOLD=0.55

# Review modes:
#   OFF  – no review
#   SUMMARY – only global review summary in PR comment
#   INLINE  – summary + inline comments
#   DEEP   – inline + test suggestions + risk sections
REVIEW_MODE = os.getenv("REVIEW_MODE", "DEEP").upper()

# CI modes:
#   0 – disabled
#   1 – log-only
#   2 – single retry
#   3 – full retry loop with follow-up patches
CI_MODE = int(os.getenv("CI_MODE", "3"))


if not GITHUB_TOKEN:
    # Don't crash import-time; main() will fail loudly with a better message
    pass