from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .config import DATA_DIR
from .models import ScrapeProgress, Submission


def _submissions_path(arena: str) -> Path:
    return DATA_DIR / f"{arena}_submissions.jsonl"


def _progress_path(arena: str) -> Path:
    return DATA_DIR / f"{arena}_progress.json"


def _failed_path(arena: str) -> Path:
    return DATA_DIR / f"{arena}_failed.json"


def save_submission(submission: Submission, arena: str) -> None:
    path = _submissions_path(arena)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(submission.model_dump_json() + "\n")


def load_scraped_ids(arena: str) -> set[str]:
    path = _submissions_path(arena)
    ids: set[str] = set()
    if not path.exists():
        return ids
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            ids.add(obj["submission_id"])
    return ids


def save_progress(progress: ScrapeProgress) -> None:
    path = _progress_path(progress.arena)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write(progress.model_dump_json(indent=2))


def load_progress(arena: str) -> ScrapeProgress:
    path = _progress_path(arena)
    if path.exists():
        return ScrapeProgress.model_validate_json(path.read_text())
    return ScrapeProgress(arena=arena)


def save_failed_id(arena: str, submission_id: str, error: str) -> None:
    path = _failed_path(arena)
    path.parent.mkdir(parents=True, exist_ok=True)
    failed: list[dict] = []
    if path.exists():
        failed = json.loads(path.read_text())
    failed.append({
        "submission_id": submission_id,
        "error": error,
        "timestamp": datetime.utcnow().isoformat(),
    })
    path.write_text(json.dumps(failed, indent=2))


def load_failed_ids(arena: str) -> set[str]:
    path = _failed_path(arena)
    if not path.exists():
        return set()
    failed = json.loads(path.read_text())
    return {item["submission_id"] for item in failed}
