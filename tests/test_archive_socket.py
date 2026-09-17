"""Phase 31 U1–U4, U17 and Phase 32 V4, V11–V17, V20, V21 on a real socket.

Every archive session any test in this module produces is also checked against
V17: outcome = Completed exactly when bytes_sent = declared_bytes.
"""
import io
import logging
import os
import threading
import time
import zipfile

import pytest

import backend.app.services.photo_files as photo_files
from backend.app.config import Settings
from backend.app.services.archive_status import get_status
from backend.app.services.photo_files import ROOT, _file_index_lock, _file_indexes

from .archive_harness import (
    DATE_FOLDER, CapturingHandler, LiveServer, RawDownload, reset_archive_state,
    session_records, wait_for, wait_terminal,
)

LIVENESS = dict(
    shipping_photos_archive_send_stall_seconds=1.0,
    shipping_photos_archive_preflight_stall_seconds=1.0,
    shipping_photos_archive_reader_stall_seconds=30.0,
    shipping_photos_archive_session_budget_seconds=60.0,
    shipping_photos_archive_read_chunk_bytes=65_536,
    shipping_photos_archive_credit_poll_seconds=0.02,
)


@pytest.fixture
def logs(monkeypatch):
    handler = CapturingHandler()
    root = logging.getLogger()
    monkeypatch.setattr(root, "level", logging.INFO)
    root.addHandler(handler)
    yield handler
    root.removeHandler(handler)
    for record in session_records(handler.snapshot()):
        if record.declared_bytes is not None:
            assert (record.outcome == "Completed") == (record.bytes_sent == record.declared_bytes), record


@pytest.fixture
def photos(tmp_path):
    folder = tmp_path / DATE_FOLDER
    folder.mkdir()
    return folder


@pytest.fixture
def make_server(tmp_path, monkeypatch, logs):
    reset_archive_state(monkeypatch)
    servers = []

    def build(**overrides) -> LiveServer:
        settings = Settings(shipping_photos_dir=str(tmp_path), **{**LIVENESS, **overrides})
        server = LiveServer(settings).start()
        servers.append(server)
        return server

    yield build
    for server in servers:
        server.stop()


def write(folder, name, size):
    (folder / name).write_bytes(os.urandom(size))


def asgi_errors(handler):
    return [r for r in handler.snapshot() if "Exception in ASGI application" in r.getMessage()]


def permits_idle(server):
    permits = server.permits
    return permits is not None and permits.in_use == 0 and permits.live_readers == 0


# ---- V4 ---------------------------------------------------------------------

def test_v4_declared_length_survives_the_stack(make_server, photos):
    write(photos, "a.jpg", 3_000_000)
    write(photos, "b.jpg", 1_000)
    server = make_server()
    token = server.mint()
    download = RawDownload(server, token, accept_encoding="gzip, deflate, br")
    download.read_head()
    result = download.read_all()

    assert result.status == 200
    assert result.declared is not None
    assert result.headers.get("content-encoding") != "gzip"
    assert result.headers["accept-ranges"] == "none"
    assert result.http_complete
    with zipfile.ZipFile(io.BytesIO(result.body)) as archive:
        assert archive.testzip() is None
        assert archive.namelist() == ["a.jpg", "b.jpg"]
    assert wait_terminal(token, server.settings).outcome == "Completed"


# ---- U2 ---------------------------------------------------------------------

def test_u2_slow_client_is_not_abandoned(make_server, photos, logs):
    write(photos, "a.jpg", 2_000_000)
    server = make_server()
    token = server.mint()
    download = RawDownload(server, token)
    download.read_head()
    started = time.monotonic()
    result = download.read_all(rate_bytes_per_second=600_000)

    assert time.monotonic() - started > 3 * server.settings.shipping_photos_archive_send_stall_seconds
    assert result.http_complete
    assert wait_terminal(token, server.settings).outcome == "Completed"
    assert all(r.outcome != "AbandonedStall" for r in session_records(logs.snapshot()))


# ---- U1, U3, U4, V15 disconnect ---------------------------------------------

def test_u1_dead_client_recovers_the_permit_within_the_send_bound(make_server, photos, logs):
    write(photos, "big.jpg", 40_000_000)
    server = make_server()
    token = server.mint()
    download = RawDownload(server, token, receive_buffer=4096)
    download.read_head()
    download.read_some(100_000)
    assert wait_for(lambda: server.permits.in_use == 1, 5)
    reset_at = time.monotonic()
    download.reset()

    bound = server.settings.shipping_photos_archive_send_stall_seconds
    assert wait_for(lambda: permits_idle(server), bound + 3.0)
    assert time.monotonic() - reset_at < bound + 3.0
    status = wait_terminal(token, server.settings)
    assert status.outcome == "AbandonedDisconnect"
    assert all(r.outcome != "AbandonedStall" for r in session_records(logs.snapshot()))
    assert not asgi_errors(logs)


def test_u3_both_permits_recover(make_server, photos, logs):
    write(photos, "big.jpg", 40_000_000)
    server = make_server()
    first, second, third = server.mint(), server.mint(), server.mint()
    clients = []
    for token in (first, second):
        client = RawDownload(server, token, receive_buffer=4096)
        client.read_head()
        client.read_some(50_000)
        clients.append(client)
    assert wait_for(lambda: server.permits.in_use == 2, 5)

    status, _, _ = server.request("GET", server.download_path(third))
    assert status == 503

    for client in clients:
        client.reset()
    assert wait_for(lambda: permits_idle(server), server.settings.shipping_photos_archive_send_stall_seconds + 4.0)
    assert wait_terminal(first, server.settings).outcome == "AbandonedDisconnect"
    assert wait_terminal(second, server.settings).outcome == "AbandonedDisconnect"
    assert all(r.outcome != "AbandonedStall" for r in session_records(logs.snapshot()))


# ---- V15, V16, U17 ----------------------------------------------------------

def _abandon_budget(server, photos):
    write(photos, "big.jpg", 20_000_000)
    return server.mint(), None


def _read_everything_slowly(server, token):
    download = RawDownload(server, token)
    download.read_head()
    return download.read_all(rate_bytes_per_second=400_000, timeout=30)


def test_v15_budget_abandon_is_http_incomplete_and_correlated(make_server, photos, logs):
    write(photos, "big.jpg", 20_000_000)
    server = make_server(shipping_photos_archive_session_budget_seconds=1.5)
    token = server.mint()
    result = _read_everything_slowly(server, token)

    assert result.status == 200
    assert not result.http_complete
    assert wait_terminal(token, server.settings).outcome == "AbandonedBudget"
    assert not asgi_errors(logs)

    records = logs.snapshot()
    messages = [r.getMessage() for r in records]
    incomplete = next(i for i, m in enumerate(messages) if "returned without completing response" in m)
    session_index = next(i for i, m in enumerate(messages) if m.startswith("ArchiveSessionRecord") and "AbandonedBudget" in m)
    assert session_index < incomplete


def test_v15_reader_stall_is_http_incomplete(make_server, photos, logs):
    write(photos, "big.jpg", 20_000_000)
    server = make_server(shipping_photos_archive_send_stall_seconds=4.0, shipping_photos_archive_reader_stall_seconds=1.0)
    token = server.mint()
    download = RawDownload(server, token, receive_buffer=4096)
    download.read_head()
    download.read_some(10_000)
    assert wait_for(lambda: (s := get_status(token, server.settings, time.monotonic)) is not None and s.state == "Terminal", 15)
    result = download.read_all(timeout=30)

    assert get_status(token, server.settings, time.monotonic).outcome == "AbandonedStall"
    assert not result.http_complete
    assert not asgi_errors(logs)


def test_v15_emitter_invariant_is_http_incomplete(make_server, photos, logs, monkeypatch):
    write(photos, "a.jpg", 300_000)
    server = make_server()
    original = photo_files.publish
    dropped = []

    def dropping_publish(transport, item, cancel, poll_seconds, stall_seconds=float("inf")):
        if isinstance(item, photo_files.MemberChunk) and not dropped:
            dropped.append(item)
            return "Published"
        return original(transport, item, cancel, poll_seconds, stall_seconds)

    monkeypatch.setattr(photo_files, "publish", dropping_publish)
    token = server.mint()
    download = RawDownload(server, token)
    download.read_head()
    result = download.read_all(timeout=15)

    assert not result.http_complete
    assert wait_terminal(token, server.settings).outcome == "FailedFraming"
    assert not asgi_errors(logs)


def test_v16_length_guard_refuses_before_the_protocol_does(make_server, photos, logs, monkeypatch):
    write(photos, "a.jpg", 50_000)
    server = make_server()
    original = photo_files.build_archive_plan

    def short_plan(*args, **kwargs):
        plan = original(*args, **kwargs)
        plan.declared_bytes -= 1
        return plan

    monkeypatch.setattr(photo_files, "build_archive_plan", short_plan)
    token = server.mint()
    download = RawDownload(server, token)
    download.read_head()
    result = download.read_all(timeout=15)

    assert result.declared is not None
    assert len(result.body) < result.declared
    assert wait_terminal(token, server.settings).outcome == "FailedFraming"
    assert not asgi_errors(logs)
    assert not [r for r in logs.snapshot() if "Content-Length" in r.getMessage()]


# ---- V13, V14 ---------------------------------------------------------------

def _mutate(path, how):
    if how == "grow":
        with open(path, "ab") as handle:
            handle.write(b"more")
    elif how == "shrink":
        with open(path, "r+b") as handle:
            handle.truncate(10)
    else:
        staged = path.with_suffix(".tmp")
        time.sleep(0.01)
        staged.write_bytes(os.urandom(path.stat().st_size))
        os.replace(staged, path)


@pytest.mark.parametrize("how, cause", [("grow", "Extended"), ("shrink", "Truncated"), ("replace", "Replaced")])
def test_v13_divergence_aborts_and_heals(make_server, photos, logs, monkeypatch, how, cause):
    write(photos, "a.jpg", 100_000)
    write(photos, "b.jpg", 100_000)
    server = make_server()
    original = photo_files.stream_admitted_members
    mutated = []

    def mutating_stream(session, plan):
        if not mutated:
            mutated.append(how)
            _mutate(photos / "b.jpg", how)
        return original(session, plan)

    monkeypatch.setattr(photo_files, "stream_admitted_members", mutating_stream)
    token = server.mint()
    download = RawDownload(server, token)
    download.read_head()
    result = download.read_all(timeout=15)

    assert result.status == 200
    assert not result.http_complete
    assert wait_terminal(token, server.settings).outcome == "SourceChanged"
    assert (DATE_FOLDER, ROOT) not in _file_indexes
    assert [r.divergence_cause for r in session_records(logs.snapshot()) if r.outcome == "SourceChanged"] == [cause]
    assert not asgi_errors(logs)

    retry = server.mint()
    download = RawDownload(server, retry)
    download.read_head()
    assert download.read_all(timeout=15).http_complete
    assert wait_terminal(retry, server.settings).outcome == "Completed"


def test_v14_bytes_after_the_planned_size(make_server, photos, logs, monkeypatch):
    write(photos, "a.jpg", 500_000)
    server = make_server()
    original = photo_files.publish
    appended = []

    def appending_publish(transport, item, cancel, poll_seconds, stall_seconds=float("inf")):
        published = original(transport, item, cancel, poll_seconds, stall_seconds)
        if isinstance(item, photo_files.MemberChunk) and not appended:
            appended.append(True)
            with open(photos / "a.jpg", "ab") as handle:
                handle.write(b"appended while reading")
        return published

    monkeypatch.setattr(photo_files, "publish", appending_publish)
    token = server.mint()
    download = RawDownload(server, token)
    download.read_head()
    result = download.read_all(timeout=15)

    assert not result.http_complete
    assert wait_terminal(token, server.settings).outcome == "SourceChanged"
    assert [r.divergence_cause for r in session_records(logs.snapshot())] == ["Extended"]


# ---- V11, V12 ---------------------------------------------------------------

class HeldIndexLock:
    def __init__(self):
        self.acquired = threading.Event()
        self.release_now = threading.Event()
        self.thread = threading.Thread(target=self._hold, daemon=True)

    def _hold(self):
        with _file_index_lock:
            self.acquired.set()
            self.release_now.wait(30)

    def __enter__(self):
        self.thread.start()
        self.acquired.wait(5)
        return self

    def __exit__(self, *exc):
        self.release_now.set()
        self.thread.join(5)


def test_v11_preflight_stall(make_server, photos, logs):
    write(photos, "a.jpg", 10_000)
    server = make_server()
    token = server.mint()

    with HeldIndexLock():
        status, headers, _ = server.request("GET", server.download_path(token), timeout=15)
        assert status == 503
        assert headers.get("retry-after") == "5"
        assert "content-disposition" not in {k.lower() for k in headers}
        assert get_status(token, server.settings, time.monotonic).outcome == "PreflightStalled"
        assert server.permits.in_use == 0

    assert wait_for(lambda: permits_idle(server), 5)
    download = RawDownload(server, token)
    download.read_head()
    assert download.read_all(timeout=15).http_complete
    assert wait_for(lambda: get_status(token, server.settings, time.monotonic).outcome == "Completed", 5)


def test_v12_the_loop_never_waits_on_the_index_lock(make_server, photos, logs):
    write(photos, "big.jpg", 6_000_000)
    write(photos, "other.jpg", 1_000)
    server = make_server()
    first = server.mint(["big.jpg"])
    second = server.mint(["other.jpg"])

    download = RawDownload(server, first)
    download.read_head()
    outcome = {}
    reader = threading.Thread(target=lambda: outcome.setdefault("result", download.read_all(rate_bytes_per_second=1_500_000, timeout=30)))
    reader.start()

    hold_seconds = 2 * server.settings.shipping_photos_archive_send_stall_seconds + 0.5
    latencies = []
    with HeldIndexLock():
        redemption = threading.Thread(target=lambda: server.request("GET", server.download_path(second), timeout=15))
        redemption.start()
        held_until = time.monotonic() + hold_seconds
        while time.monotonic() < held_until:
            started = time.monotonic()
            status, _, _ = server.request("GET", "/api/photos/available-dates", timeout=5)
            latencies.append(time.monotonic() - started)
            assert status == 200
            time.sleep(0.1)
    redemption.join(15)
    reader.join(30)

    assert max(latencies) < 0.25, latencies
    assert outcome["result"].http_complete
    assert wait_terminal(first, server.settings).outcome == "Completed"


# ---- V21 --------------------------------------------------------------------

def test_v21_head_cannot_spend_a_token(make_server, photos):
    write(photos, "a.jpg", 1_000)
    server = make_server()
    token = server.mint()
    status, _, _ = server.request("HEAD", server.download_path(token))
    assert status == 405
    download = RawDownload(server, token)
    download.read_head()
    assert download.read_all().http_complete


# ---- V25 --------------------------------------------------------------------

def test_v25_client_leaving_during_preflight_is_detected_before_the_plan(make_server, photos, logs):
    write(photos, "a.jpg", 50_000)
    server = make_server(shipping_photos_archive_preflight_stall_seconds=10.0)
    token = server.mint()

    with HeldIndexLock():
        download = RawDownload(server, token)
        assert wait_for(lambda: server.permits is not None and server.permits.in_use == 1, 5)
        download.reset()
        status = wait_terminal(token, server.settings, timeout=5)
        assert status.outcome == "AbandonedDisconnect"
        assert status.bytes_sent == 0
        assert server.permits.in_use == 0

    assert wait_for(lambda: permits_idle(server), 5)
    retry = RawDownload(server, token)
    retry.read_head()
    assert retry.read_all(timeout=15).http_complete


# ---- V20, live: a second redemption leaves the first session's record -------

def test_v20_second_redemption_does_not_replace_the_live_status(make_server, photos, logs):
    write(photos, "a.jpg", 50_000)
    server = make_server(shipping_photos_archive_preflight_stall_seconds=10.0)
    token = server.mint()

    with HeldIndexLock():
        first = RawDownload(server, token)
        assert wait_for(lambda: (s := get_status(token, server.settings, time.monotonic)) is not None and s.state == "Preparing", 5)
        status, _, _ = server.request("GET", server.download_path(token))
        assert status == 404
        assert get_status(token, server.settings, time.monotonic).state == "Preparing"

    first.read_head()
    assert first.read_all(timeout=15).http_complete
    assert wait_terminal(token, server.settings).outcome == "Completed"
