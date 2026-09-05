import pytest
from fastapi.testclient import TestClient
from pathlib import Path
from backend.app.config import Settings, get_settings
from backend.app.api import create_app
import backend.app.services.runtime_config as rc
from backend.app.api.deps import is_loopback_caller
from backend.app.services.photo_files import _file_indexes

app = create_app()

@pytest.fixture
def client(monkeypatch, tmp_path):
    rc._cached_config = None
    monkeypatch.setattr(rc, "load_runtime_config", lambda: {})
    _file_indexes.clear()
    from backend.app.services.archive_tokens import clear_tickets
    clear_tickets()
    
    def override_settings():
        return Settings(
            shipping_photos_dir=str(tmp_path),
            shipping_photos_archive_lan_max_files=1,
            shipping_photos_archive_lan_max_bytes=10
        )
        
    app.dependency_overrides[get_settings] = override_settings
    return TestClient(app)

def test_files_endpoint(client, tmp_path):
    (tmp_path / "2023_01_01").mkdir()
    (tmp_path / "2023_01_01" / "file.jpg").write_bytes(b"x")
    
    response = client.get("/api/photos/files?date_folder=2023_01_01")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert len(data["entries"]) == 1
    assert data["entries"][0]["name"] == "file.jpg"
    assert data["entries"][0]["previewable"] is True
    
def test_files_endpoint_not_found(client, tmp_path):
    response = client.get("/api/photos/files?date_folder=2023_01_02")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "not_found"

def test_file_endpoint(client, tmp_path):
    (tmp_path / "2023_01_01").mkdir()
    (tmp_path / "2023_01_01" / "file.jpg").write_bytes(b"image data")
    
    response = client.get("/api/photos/file/file.jpg?date_folder=2023_01_01")
    assert response.status_code == 200
    assert response.content == b"image data"
    assert "private" in response.headers["Cache-Control"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Content-Security-Policy"] == "sandbox"

def test_thumb_endpoint(client, tmp_path, monkeypatch):
    (tmp_path / "2023_01_01").mkdir()
    (tmp_path / "2023_01_01" / "file.jpg").write_bytes(b"image data")
    
    # Mock thumbnail generation to fail
    def mock_resolve(*args):
        return "err", "not_previewable"
        
    import backend.app.api.photos as ap
    monkeypatch.setattr(ap, "generate_once", mock_resolve)
    
    response = client.get("/api/photos/thumb/file.jpg?date_folder=2023_01_01")
    assert response.status_code == 415
    assert "no-store" in response.headers["Cache-Control"]

def test_thumb_endpoint_cache_unavailable(client, tmp_path, monkeypatch):
    (tmp_path / "2023_01_01").mkdir()
    (tmp_path / "2023_01_01" / "file.jpg").write_bytes(b"image data")
    
    def mock_resolve(*args):
        return "err", "cache_unavailable"
        
    import backend.app.api.photos as ap
    monkeypatch.setattr(ap, "generate_once", mock_resolve)
    
    response = client.get("/api/photos/thumb/file.jpg?date_folder=2023_01_01")
    assert response.status_code == 503
    assert "no-store" in response.headers["Cache-Control"]
    assert response.headers["Retry-After"] == "1"

def test_thumb_endpoint_success_headers(client, tmp_path, monkeypatch):
    (tmp_path / "2023_01_01").mkdir()
    (tmp_path / "2023_01_01" / "file.jpg").write_bytes(b"image data")
    
    from backend.app.services.photo_thumbnails import ThumbnailResult
    def mock_resolve(*args):
        return "ok", ThumbnailResult(path=tmp_path / "2023_01_01" / "file.jpg", media_type="image/jpeg")
        
    import backend.app.api.photos as ap
    monkeypatch.setattr(ap, "generate_once", mock_resolve)
    
    response = client.get("/api/photos/thumb/file.jpg?date_folder=2023_01_01")
    assert response.status_code == 200
    assert "private" in response.headers["Cache-Control"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Content-Security-Policy"] == "sandbox"

def test_archive_endpoint_lan_limits(client, tmp_path):
    (tmp_path / "2023_01_01").mkdir()
    (tmp_path / "2023_01_01" / "f1.jpg").write_bytes(b"x")
    (tmp_path / "2023_01_01" / "f2.jpg").write_bytes(b"y")
    
    # LAN caller (not loopback)
    app.dependency_overrides[is_loopback_caller] = lambda: False
    
    # Too many files
    response = client.post("/api/photos/archive", json={"date_folder": "2023_01_01", "selection": ["f1.jpg", "f2.jpg"]})
    assert response.status_code == 403
    assert response.json()["limit"] == "files"
    
    # Too many bytes
    (tmp_path / "2023_01_01" / "f1.jpg").write_bytes(b"x" * 20)
    _file_indexes.clear() # clear cache to re-scan
    response = client.post("/api/photos/archive", json={"date_folder": "2023_01_01", "selection": ["f1.jpg"]})
    assert response.status_code == 403
    assert response.json()["limit"] == "bytes"

def test_archive_endpoint_loopback(client, tmp_path):
    (tmp_path / "2023_01_01").mkdir()
    (tmp_path / "2023_01_01" / "f1.jpg").write_bytes(b"x")
    (tmp_path / "2023_01_01" / "f2.jpg").write_bytes(b"y")
    
    # Loopback caller
    app.dependency_overrides[is_loopback_caller] = lambda: True
    
    response = client.post("/api/photos/archive", json={"date_folder": "2023_01_01", "selection": ["f1.jpg", "f2.jpg"]})
    assert response.status_code == 200
    
    import zipfile
    import io
    zf = zipfile.ZipFile(io.BytesIO(response.content))
    names = zf.namelist()
    assert "f1.jpg" in names
    assert "f2.jpg" in names
def test_files_endpoint_calls_enqueue_warm(client, tmp_path, monkeypatch):
    (tmp_path / "2023_01_01").mkdir()
    
    calls = []
    def mock_enqueue(folder, settings):
        calls.append(folder)
        
    monkeypatch.setattr("backend.app.services.photo_warm.enqueue_warm", mock_enqueue)
    
    response = client.get("/api/photos/files?date_folder=2023_01_01")
    assert response.status_code == 200
    assert calls == ["2023_01_01"]
    
def test_files_endpoint_enqueue_warm_raises(client, tmp_path, monkeypatch):
    (tmp_path / "2023_01_01").mkdir()
    
    def mock_enqueue(folder, settings):
        raise ValueError("Boom")
        
    monkeypatch.setattr("backend.app.services.photo_warm.enqueue_warm", mock_enqueue)
    
    response = client.get("/api/photos/files?date_folder=2023_01_01")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_thumb_endpoint_saturated_returns_503_no_store(client, tmp_path, monkeypatch):
    """Patch 05 8.3: unchanged from Phase 26, asserted here because Part 2
    rewrote the code path that produces it."""
    (tmp_path / "2023_01_01").mkdir()
    (tmp_path / "2023_01_01" / "file.jpg").write_bytes(b"image data")

    # Permits exhausted AND the admission bound already full, so the request is
    # rejected without queueing rather than waiting out queue_wait_seconds.
    import backend.app.services.photo_thumbnails as pt
    monkeypatch.setattr(pt, "_gate_active", 999)
    monkeypatch.setattr(pt, "_gate_waiting", 999)

    response = client.get("/api/photos/thumb/file.jpg?date_folder=2023_01_01")

    assert response.status_code == 503
    assert response.json()["kind"] in ("saturated", "timeout")
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Retry-After"] == "1"
import pytest
import io
import zipfile
import threading
from backend.app.api.deps import is_loopback_caller
from backend.app.services.photo_files import _file_indexes
from backend.app.api.photos import hold_permit_across_stream
from backend.app.services.archive_tokens import issue_ticket, ArchiveTicket, _tickets

def test_archive_token_lan_cap_files(client, tmp_path):
    (tmp_path / "2023_01_01").mkdir()
    (tmp_path / "2023_01_01" / "f1.jpg").write_bytes(b"x")
    (tmp_path / "2023_01_01" / "f2.jpg").write_bytes(b"y")
    client.app.dependency_overrides[is_loopback_caller] = lambda: False
    
    response = client.post("/api/photos/archive-token", json={"date_folder": "2023_01_01", "selection": ["f1.jpg", "f2.jpg"]})
    assert response.status_code == 403
    assert response.json()["limit"] == "files"

def test_archive_token_lan_cap_bytes(client, tmp_path):
    (tmp_path / "2023_01_01").mkdir()
    (tmp_path / "2023_01_01" / "f1.jpg").write_bytes(b"x" * 20)
    client.app.dependency_overrides[is_loopback_caller] = lambda: False
    
    response = client.post("/api/photos/archive-token", json={"date_folder": "2023_01_01", "selection": ["f1.jpg"]})
    assert response.status_code == 403
    assert response.json()["limit"] == "bytes"

def test_archive_token_not_found(client):
    response = client.post("/api/photos/archive-token", json={"date_folder": "2023_01_02", "selection": []})
    assert response.status_code == 404

def test_archive_download_round_trip(client, tmp_path):
    (tmp_path / "2023_01_01").mkdir()
    (tmp_path / "2023_01_01" / "f1.jpg").write_bytes(b"x")
    
    res1 = client.post("/api/photos/archive-token", json={"date_folder": "2023_01_01", "selection": ["f1.jpg"]})
    assert res1.status_code == 200
    token = res1.json()["token"]
    
    res2 = client.get(f"/api/photos/archive-download?token={token}")
    assert res2.status_code == 200
    assert res2.headers["Content-Type"] == "application/zip"
    
    zf = zipfile.ZipFile(io.BytesIO(res2.content))
    assert "f1.jpg" in zf.namelist()

def test_archive_download_token_reusable(client, tmp_path):
    (tmp_path / "2023_01_01").mkdir()
    (tmp_path / "2023_01_01" / "f1.jpg").write_bytes(b"x")
    
    res1 = client.post("/api/photos/archive-token", json={"date_folder": "2023_01_01", "selection": ["f1.jpg"]})
    token = res1.json()["token"]
    
import pytest
import io
import zipfile
import threading
from backend.app.api.deps import is_loopback_caller
from backend.app.services.photo_files import _file_indexes
from backend.app.api.photos import hold_permit_across_stream
from backend.app.services.archive_tokens import issue_ticket, ArchiveTicket, _tickets

def test_archive_token_lan_cap_files(client, tmp_path):
    (tmp_path / "2023_01_01").mkdir()
    (tmp_path / "2023_01_01" / "f1.jpg").write_bytes(b"x")
    (tmp_path / "2023_01_01" / "f2.jpg").write_bytes(b"y")
    client.app.dependency_overrides[is_loopback_caller] = lambda: False
    
    response = client.post("/api/photos/archive-token", json={"date_folder": "2023_01_01", "selection": ["f1.jpg", "f2.jpg"]})
    assert response.status_code == 403
    assert response.json()["limit"] == "files"

def test_archive_token_lan_cap_bytes(client, tmp_path):
    (tmp_path / "2023_01_01").mkdir()
    (tmp_path / "2023_01_01" / "f1.jpg").write_bytes(b"x" * 20)
    client.app.dependency_overrides[is_loopback_caller] = lambda: False
    
    response = client.post("/api/photos/archive-token", json={"date_folder": "2023_01_01", "selection": ["f1.jpg"]})
    assert response.status_code == 403
    assert response.json()["limit"] == "bytes"

def test_archive_token_not_found(client):
    response = client.post("/api/photos/archive-token", json={"date_folder": "2023_01_02", "selection": []})
    assert response.status_code == 404

def test_archive_download_round_trip(client, tmp_path):
    (tmp_path / "2023_01_01").mkdir()
    (tmp_path / "2023_01_01" / "f1.jpg").write_bytes(b"x")
    
    res1 = client.post("/api/photos/archive-token", json={"date_folder": "2023_01_01", "selection": ["f1.jpg"]})
    assert res1.status_code == 200
    token = res1.json()["token"]
    
    res2 = client.get(f"/api/photos/archive-download?token={token}")
    assert res2.status_code == 200
    assert res2.headers["Content-Type"] == "application/zip"
    
    zf = zipfile.ZipFile(io.BytesIO(res2.content))
    assert "f1.jpg" in zf.namelist()

def test_archive_download_token_reusable(client, tmp_path):
    (tmp_path / "2023_01_01").mkdir()
    (tmp_path / "2023_01_01" / "f1.jpg").write_bytes(b"x")
    
    res1 = client.post("/api/photos/archive-token", json={"date_folder": "2023_01_01", "selection": ["f1.jpg"]})
    token = res1.json()["token"]
    
    res2 = client.get(f"/api/photos/archive-download?token={token}")
    assert res2.status_code == 200
    
    res3 = client.get(f"/api/photos/archive-download?token={token}")
    assert res3.status_code == 200
def test_archive_download_bad_token(client):
    res = client.get("/api/photos/archive-download?token=0123456789abcdef0123456789abcdef")
    assert res.status_code == 404

def test_archive_download_token_expired(client, tmp_path, monkeypatch):
    (tmp_path / "2023_01_01").mkdir()
    (tmp_path / "2023_01_01" / "f1.jpg").write_bytes(b"x")
    
    res1 = client.post("/api/photos/archive-token", json={"date_folder": "2023_01_01", "selection": []})
    token = res1.json()["token"]
    
    import time
    monkeypatch.setattr(time, "monotonic", lambda: 9999999.0)
    
    res2 = client.get(f"/api/photos/archive-download?token={token}")
    assert res2.status_code == 404

def test_archive_download_loopback_token_rejected_from_lan(client, tmp_path):
    (tmp_path / "2023_01_01").mkdir()
    (tmp_path / "2023_01_01" / "f1.jpg").write_bytes(b"x")
    
    client.app.dependency_overrides[is_loopback_caller] = lambda: True
    res1 = client.post("/api/photos/archive-token", json={"date_folder": "2023_01_01", "selection": []})
    token = res1.json()["token"]
    
    client.app.dependency_overrides[is_loopback_caller] = lambda: False
    res2 = client.get(f"/api/photos/archive-download?token={token}")
    assert res2.status_code == 403

def test_hold_permit_released_on_generator_close():
    sem = threading.Semaphore(1)
    sem.acquire()
    
    def gen():
        yield b"chunk"
        yield b"chunk2"
        
    iterator = hold_permit_across_stream(gen(), sem)
    assert next(iterator) == b"chunk"
    
    iterator.close()
    
    assert sem.acquire(blocking=False) is True

def test_archive_download_not_gzipped(client, tmp_path):
    (tmp_path / "2023_01_01").mkdir()
    (tmp_path / "2023_01_01" / "f1.jpg").write_bytes(b"x")
    
    res1 = client.post("/api/photos/archive-token", json={"date_folder": "2023_01_01", "selection": []})
    token = res1.json()["token"]
    
    res2 = client.get(f"/api/photos/archive-download?token={token}", headers={"Accept-Encoding": "gzip"})
    assert "gzip" not in res2.headers.get("Content-Encoding", "")
    assert res2.headers.get("Content-Encoding", "") == "identity"

def test_issue_ticket_stamps_clock_inside_lock():
    import backend.app.services.archive_tokens as at
    from backend.app.config import Settings
    ticket = ArchiveTicket("2023_01_01", [], "file.zip", False, issued_at=999.0)
    
    at.clear_tickets()
    token = at.issue_ticket(ticket, Settings(), lambda: 100.0)
    
    stored = at._tickets[token]
    assert stored.issued_at == 100.0
