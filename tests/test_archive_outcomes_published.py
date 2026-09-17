"""Phase 31 U12 / Phase 32 V24 — the frontend's outcome lists mirror the server's.

frontend/src/composables/archiveProgress.ts publishes SESSION_OUTCOMES and
REJECTION_REASONS; archiveProgress.spec.ts asserts every member has a message.
This test closes the loop from the server side, so a new constant cannot ship
as a blank toast.
"""
import re
from pathlib import Path
from typing import get_args

from backend.app.services.archive_status import RejectionReason, SessionOutcome
from backend.app.services import photo_files

SOURCE = Path(__file__).resolve().parents[1] / "frontend" / "src" / "composables" / "archiveProgress.ts"


def published(name: str) -> list:
    text = SOURCE.read_text(encoding="utf-8")
    match = re.search(rf"export const {name} = \[(.*?)\] as const;", text, re.S)
    assert match, f"{name} not found in {SOURCE}"
    return re.findall(r"'([A-Za-z]+)'", match.group(1))


def test_session_outcomes_are_published_to_the_frontend():
    assert published("SESSION_OUTCOMES") == list(get_args(SessionOutcome))


def test_rejection_reasons_are_published_to_the_frontend():
    assert published("REJECTION_REASONS") == list(get_args(RejectionReason))


def test_the_session_and_the_store_share_one_outcome_vocabulary():
    assert set(get_args(photo_files.SessionOutcome)) == set(get_args(SessionOutcome))
