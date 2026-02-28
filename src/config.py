from pathlib import Path

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
AUTH_DIR = PROJECT_ROOT / "auth"
DATA_DIR = PROJECT_ROOT / "data"
AUTH_STATE_FILE = AUTH_DIR / "browser_state.json"

# URLs
BASE_URL = "https://app.grayswan.ai"
ARENA_BASE = f"{BASE_URL}/arena"


def submissions_list_url(arena: str) -> str:
    return f"{ARENA_BASE}/challenge/{arena}/submissions"


def submission_detail_url(arena: str, chat_id: str, submission_id: str) -> str:
    return f"{ARENA_BASE}/challenge/{arena}/chat/{chat_id}?submissionId={submission_id}"


# Arenas
DEFAULT_ARENAS = ["proving-ground", "safeguards"]

# Scraping behavior
PAGE_SIZE = 10
REQUEST_DELAY_MIN = 1.5
REQUEST_DELAY_MAX = 3.0
PAGE_LOAD_TIMEOUT = 30_000  # ms
NAVIGATION_TIMEOUT = 60_000  # ms
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 5  # seconds, multiplied by attempt number
