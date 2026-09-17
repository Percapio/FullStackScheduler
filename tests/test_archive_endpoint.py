"""Phase 31 U5–U10, U14, U15 and Phase 32 V18–V20, V22, in-process.

Layout and store properties only. Liveness lives in test_archive_socket.py.
"""
import asyncio
import inspect
import io
import logging
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

import backend.app.services.archive_status as archive_status
import backend.app.services.archive_tokens as archive_tokens
from backend.app.api import create_app
from backend.app.api.deps import is_loopback_caller
from backend.app.api.photos import ArchiveStreamingResponse
from backend.app.config import Settings, get_settings
from backend.app.services.archive_status import get_status, record_status
from backend.app.services.archive_zip import inline_member, lay_out
from backend.app.services.photo_files import (
    ArchivePermits, ArchiveStreamSession, ArchiveTransport, emit_archive, invalidate_file_index,
    record_outcome, release, try_admit,
)

from .archive_harness import DATE_FOLDER, CapturingHandler, reset_archive_state, session_records


@pytest.fixture
def settings(tmp_path):
    return Settings(shipping_photos_dir=str(tmp_path))


@pytest.fixture
def photos(tmp_path):
    folder = tmp_path / DATE_FOLDER
    folder.mkdir()
    return folder


@pytest.fixture
def app(settings, monkeypatch):
    reset_archive_state(monkeypatch)
    application = create_app()
    application.dependency_overrides[get_settings] = lambda: settings
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def logs(monkeypatch):
    handler = CapturingHandler()
    root = logging.getLogger()
    monkeypatch.setattr(root, "level", logging.INFO)
    root.addHandler(handler)
    yield handler
    root.removeHandler(handler)


def mint(client, selection=None):
    response = client.post("/api/photos/archive-token", json={"date_folder": DATE_FOLDER, "selection": selection or []})
    assert response.status_code == 200, response.text
    return response.json()["token"]


def download(client, token):
    return client.get("/api/photos/archive-download", params={"token": token})


# ---- Archive content, end to end --------------------------------------------

def test_archive_streams_every_member_with_its_size(client, photos):
    (photos / "a.jpg").write_bytes(b"a" * 1024)
    (photos / "b.jpg").write_bytes(b"b")
    response = download(client, mint(client))
    assert response.status_code == 200
    assert int(response.headers["content-length"]) == len(response.content)
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.getinfo("a.jpg").file_size == 1024
        assert archive.read("b.jpg") == b"b"


def test_full_selection_of_a_truncated_listing_carries_truncated(tmp_path, photos, monkeypatch):
    reset_archive_state(monkeypatch)
    settings = Settings(shipping_photos_dir=str(tmp_path), shipping_photos_max_files_per_folder=2)
    application = create_app()
    application.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(application)
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        (photos / name).write_bytes(name.encode())
    response = download(client, mint(client))
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == ["a.jpg", "b.jpg", "_TRUNCATED.txt"]
        assert archive.read("_TRUNCATED.txt") == b"Listing was truncated to 2 files."


def test_u14_completion_with_unresolved_entries_is_distinguished(client, photos, settings):
    (photos / "a.jpg").write_bytes(b"a")
    token = mint(client, ["a.jpg", "absent.jpg"])
    response = download(client, token)
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == ["a.jpg", "_MISSING.txt"]
    body = client.get("/api/photos/archive-status", params={"token": token}).json()
    assert body == {"state": "Terminal", "outcome": "Completed", "bytes_sent": len(response.content),
                    "entry_count": 1, "unresolved_count": 1}


# ---- U6, V20 ----------------------------------------------------------------

def test_u6_rejections_are_recorded_and_hold_no_permit(client, app, photos, settings, logs):
    (photos / "a.jpg").write_bytes(b"a")
    spent = mint(client)
    assert download(client, spent).status_code == 200
    assert download(client, spent).status_code == 404
    assert get_status(spent, settings, time.monotonic).outcome == "Completed"

    unknown = "u" * 32
    assert download(client, unknown).status_code == 404
    assert get_status(unknown, settings, time.monotonic).outcome == "TokenExpired"

    app.dependency_overrides[is_loopback_caller] = lambda: True
    scoped = mint(client)
    app.dependency_overrides[is_loopback_caller] = lambda: False
    assert download(client, scoped).status_code == 403
    app.dependency_overrides.pop(is_loopback_caller)

    busy = mint(client)
    app.state.archive_permits = ArchivePermits(capacity=0, reader_ceiling=4)
    assert download(client, busy).status_code == 503
    assert get_status(busy, settings, time.monotonic).outcome == "PermitsExhausted"

    rejected = [r.getMessage() for r in logs.snapshot() if r.getMessage().startswith("ArchiveRejected")]
    assert any("reason=TokenSpent" in m for m in rejected)
    assert any("reason=TokenScope" in m for m in rejected)
    assert app.state.archive_permits.in_use == 0


@pytest.mark.parametrize("break_folder, reason", [("delete", "FolderNotFound"), ("unconfigure", "ListingUnavailable")])
def test_v20_folder_refusals_settle_through_release(tmp_path, photos, monkeypatch, logs, break_folder, reason):
    reset_archive_state(monkeypatch)
    active = {"settings": Settings(shipping_photos_dir=str(tmp_path))}
    application = create_app()
    application.dependency_overrides[get_settings] = lambda: active["settings"]
    client = TestClient(application)
    (photos / "a.jpg").write_bytes(b"a")
    token = mint(client)

    if break_folder == "delete":
        moved = photos.with_name("moved")
        photos.rename(moved)
    else:
        active["settings"] = Settings(shipping_photos_dir="")
    invalidate_file_index(DATE_FOLDER)

    response = download(client, token)
    assert response.status_code == 404
    assert get_status(token, active["settings"], time.monotonic).outcome == reason
    messages = [r.getMessage() for r in logs.snapshot()]
    assert not [m for m in messages if m.startswith("ArchiveRejected")]
    assert [r.outcome for r in session_records(logs.snapshot())] == [reason]

    if break_folder == "delete":
        moved.rename(photos)
    active["settings"] = Settings(shipping_photos_dir=str(tmp_path))
    invalidate_file_index(DATE_FOLDER)
    archive_status.clear_status()
    assert download(client, token).status_code == 200


# ---- U5, U7, U9, U15, V18, V19 ----------------------------------------------

def make_session(loop, settings, token="t" * 32, minted_loopback=False):
    permits = ArchivePermits(2, 4)
    return ArchiveStreamSession(
        session_id="s" * 16, transport=ArchiveTransport(3, loop), lease=try_admit(permits), token=token,
        date_folder=DATE_FOLDER, sub_folder="", selection=[], minted_loopback=minted_loopback, settings=settings,
    )


def test_u5_terminal_status_is_written_exactly_once(settings, logs, monkeypatch):
    reset_archive_state(monkeypatch)

    async def scenario():
        session = make_session(asyncio.get_running_loop(), settings)
        release(session, "AbandonedBudget")
        release(session, "Completed")
        assert record_outcome(session, "FailedFraming") == "AlreadyRecorded"
        return session

    session = asyncio.run(scenario())
    assert get_status(session.token, settings, time.monotonic).outcome == "AbandonedBudget"
    assert [r.outcome for r in session_records(logs.snapshot())] == ["AbandonedBudget"]
    assert session.lease.permits.in_use == 0


def test_u7_u15_status_outlives_the_ticket_and_then_disappears(settings, monkeypatch):
    reset_archive_state(monkeypatch)
    token = "k" * 32
    record_status(token, "Terminal", "Completed", 1, 1, 0, False, settings, lambda: 1000.0)
    ttl = settings.shipping_photos_archive_token_ttl_seconds
    grace = settings.shipping_photos_archive_status_grace_seconds
    assert get_status(token, settings, lambda: 1000.0 + ttl + 1).outcome == "Completed"
    assert get_status(token, settings, lambda: 1000.0 + ttl + grace - 1) is not None
    assert get_status(token, settings, lambda: 1000.0 + ttl + grace + 1) is None
    assert token in archive_status._status_store


def test_u9_status_store_is_bounded_least_recently_written(settings, monkeypatch):
    reset_archive_state(monkeypatch)
    limit = settings.shipping_photos_archive_status_max
    tokens = [f"{i:032d}" for i in range(limit + 32)]
    for i, token in enumerate(tokens):
        record_status(token, "Terminal", "Completed", 0, 0, 0, False, settings, lambda i=i: 1000.0 + i)
    assert len(archive_status._status_store) == limit
    assert list(archive_status._status_store) == tokens[-limit:]


def test_v18_one_clock_convention():
    for module, names in ((archive_status, ("record_status", "get_status")),
                          (archive_tokens, ("issue_ticket", "inspect_ticket", "bind_ticket"))):
        for name in names:
            annotation = inspect.signature(getattr(module, name)).parameters["clock"].annotation
            assert "Callable" in str(annotation), (name, annotation)


def test_v18_u10_unknown_status_is_200_and_polls_log_nothing(client, logs):
    for _ in range(1000):
        response = client.get("/api/photos/archive-status", params={"token": "z" * 32})
        assert response.status_code == 200
        assert response.json() == {"state": "Unknown"}
        assert response.headers["cache-control"] == "no-store"
    assert not [r for r in logs.snapshot() if r.name.startswith(("backend", "scheduler"))]


def test_u8_v19_terminal_status_keeps_its_scope_after_eviction(client, app, settings, monkeypatch):
    token = "l" * 32

    async def scenario():
        session = make_session(asyncio.get_running_loop(), settings, token=token, minted_loopback=True)
        record_status(token, "Preparing", None, 0, 0, 0, True, settings, time.monotonic)
        archive_status.clear_status()
        release(session, "Completed")

    asyncio.run(scenario())
    app.dependency_overrides[is_loopback_caller] = lambda: False
    assert client.get("/api/photos/archive-status", params={"token": token}).status_code == 403
    app.dependency_overrides[is_loopback_caller] = lambda: True
    assert client.get("/api/photos/archive-status", params={"token": token}).json()["outcome"] == "Completed"


def test_v20_rejection_never_replaces_a_live_record(settings, monkeypatch):
    reset_archive_state(monkeypatch)
    token = "m" * 32
    record_status(token, "Streaming", None, 0, 1, 0, True, settings, time.monotonic)
    record_status(token, "Terminal", "TokenSpent", 0, 0, 0, False, settings, time.monotonic)
    record_status(token, "Terminal", "TicketRefused", 0, 0, 0, False, settings, time.monotonic)
    assert get_status(token, settings, time.monotonic).state == "Streaming"
    record_status(token, "Terminal", "Completed", 10, 1, 0, True, settings, time.monotonic)
    assert get_status(token, settings, time.monotonic).outcome == "Completed"


# ---- V22 --------------------------------------------------------------------

def test_v22_disconnect_during_the_central_directory_is_not_failed_framing(settings, monkeypatch):
    reset_archive_state(monkeypatch)

    async def scenario():
        session = make_session(asyncio.get_running_loop(), settings)
        plan = lay_out([inline_member("a.txt", b"hello"), inline_member("b.txt", b"world")])
        session.plan = plan
        sent = []

        async def send(message):
            body = message.get("body", b"")
            if body.startswith(b"PK\x01\x02"):
                await asyncio.sleep(30)
            sent.append(message)

        response = ArchiveStreamingResponse(emit_archive(session, plan), session=session, stall_seconds=0.2)
        await response.stream_response(send)
        return session, sent

    session, sent = asyncio.run(scenario())
    assert session.outcome == "AbandonedDisconnect"
    assert session.released
    assert not any(m.get("more_body") is False for m in sent)
    assert get_status(session.token, settings, time.monotonic).outcome == "AbandonedDisconnect"


def test_v20_preparing_starts_the_authoritative_redemption(settings, monkeypatch):
    reset_archive_state(monkeypatch)
    token = "n" * 32
    record_status(token, "Terminal", "PreflightStalled", 0, 0, 0, True, settings, time.monotonic)
    record_status(token, "Streaming", None, 0, 1, 0, True, settings, time.monotonic)
    assert get_status(token, settings, time.monotonic).outcome == "PreflightStalled"
    record_status(token, "Preparing", None, 0, 0, 0, False, settings, time.monotonic)
    status = get_status(token, settings, time.monotonic)
    assert (status.state, status.minted_loopback) == ("Preparing", True)
    record_status(token, "Terminal", "Completed", 5, 1, 0, True, settings, time.monotonic)
    assert get_status(token, settings, time.monotonic).outcome == "Completed"
