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
    monkeypatch.setattr(ap, "resolve_thumbnail", mock_resolve)
    
    response = client.get("/api/photos/thumb/file.jpg?date_folder=2023_01_01")
    assert response.status_code == 415
    assert "no-store" in response.headers["Cache-Control"]

def test_thumb_endpoint_cache_unavailable(client, tmp_path, monkeypatch):
    (tmp_path / "2023_01_01").mkdir()
    (tmp_path / "2023_01_01" / "file.jpg").write_bytes(b"image data")
    
    def mock_resolve(*args):
        return "err", "cache_unavailable"
        
    import backend.app.api.photos as ap
    monkeypatch.setattr(ap, "resolve_thumbnail", mock_resolve)
    
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
    monkeypatch.setattr(ap, "resolve_thumbnail", mock_resolve)
    
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
