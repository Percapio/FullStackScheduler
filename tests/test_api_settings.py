import json
import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings, get_settings
from backend.app.api import create_app
import backend.app.services.runtime_config as rc
import backend.app.api.settings as api_settings

app = create_app()

@pytest.fixture
def client(monkeypatch, tmp_path):
    rc._cached_config = None
    monkeypatch.setattr(rc, "_runtime_root", lambda: tmp_path)
    
    def override_settings():
        return Settings(shipping_photos_dir="C:\\env")
        
    app.dependency_overrides[get_settings] = override_settings
    return TestClient(app)

def test_loopback_gate():
    from fastapi import Request
    
    class MockClient:
        def __init__(self, host):
            self.host = host
            
    class MockRequest:
        def __init__(self, host):
            self.client = MockClient(host) if host is not None else None
            
    from backend.app.api.settings import is_loopback_caller
    assert is_loopback_caller(MockRequest("127.0.0.1")) is True
    assert is_loopback_caller(MockRequest("::1")) is True
    assert is_loopback_caller(MockRequest("192.168.1.5")) is False
    assert is_loopback_caller(MockRequest(None)) is False

def test_get_photos_dir_non_loopback(client):
    response = client.get("/api/settings/photos-dir")
    assert response.status_code == 200
    data = response.json()
    assert data["editable"] is False
    assert data["path"] is None
    assert data["configured"] is True
    assert "C:\\env" not in response.text # Leak check

def test_get_photos_dir_loopback(client):
    app.dependency_overrides[api_settings.is_loopback_caller] = lambda: True
    
    response = client.get("/api/settings/photos-dir")
    assert response.status_code == 200
    data = response.json()
    assert data["editable"] is True
    assert data["path"] == "C:\\env"
    assert data["configured"] is True
    app.dependency_overrides.pop(api_settings.is_loopback_caller)
    
def test_get_photos_dir_unconfigured_loopback(client):
    app.dependency_overrides[api_settings.is_loopback_caller] = lambda: True
    
    app.dependency_overrides[get_settings] = lambda: Settings(shipping_photos_dir="")
    response = client.get("/api/settings/photos-dir")
    assert response.status_code == 200
    data = response.json()
    assert data["editable"] is True
    assert data["path"] is None
    assert data["configured"] is False
    app.dependency_overrides.clear()

def test_browse_non_loopback(client):
    response = client.get("/api/settings/browse")
    assert response.status_code == 403

def test_browse_loopback(client, tmp_path):
    app.dependency_overrides[api_settings.is_loopback_caller] = lambda: True
    
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "c").touch() # file
    
    response = client.get(f"/api/settings/browse?path={tmp_path}")
    assert response.status_code == 200
    data = response.json()
    assert not data["truncated"]
    
    names = [e["name"] for e in data["entries"]]
    assert "a" in names
    assert "b" in names
    assert "c" not in names
    
    assert data["parent"] == str(tmp_path.parent)
    app.dependency_overrides.pop(api_settings.is_loopback_caller)

def test_browse_filtering_and_truncation(client, tmp_path):
    app.dependency_overrides[api_settings.is_loopback_caller] = lambda: True
    
    app.dependency_overrides[get_settings] = lambda: Settings(settings_browse_max_entries=2)
    
    (tmp_path / "dog1").mkdir()
    (tmp_path / "dog2").mkdir()
    (tmp_path / "dog3").mkdir()
    (tmp_path / "cat").mkdir()
    
    response = client.get(f"/api/settings/browse?path={tmp_path}&prefix=dog")
    assert response.status_code == 200
    data = response.json()
    assert data["truncated"] is True
    
    names = [e["name"] for e in data["entries"]]
    assert len(names) == 2
    assert "dog1" in names
    assert "dog2" in names
    assert "dog3" not in names
    assert "cat" not in names
    app.dependency_overrides.clear()

def test_browse_semaphore(client):
    app.dependency_overrides[api_settings.is_loopback_caller] = lambda: True
    
    api_settings._browse_semaphore = None
    settings = Settings(settings_browse_max_concurrent=1)
    app.dependency_overrides[get_settings] = lambda: settings
    
    sem = api_settings._get_browse_semaphore(settings)
    assert sem.acquire(blocking=False)
    
    response = client.get("/api/settings/browse?path=C:\\")
    assert response.status_code == 503
    assert response.json()["detail"]["kind"] == "busy"
    
    sem.release()
    
    response = client.get("/api/settings/browse?path=some_nonexistent_path")
    assert response.status_code == 404
    app.dependency_overrides.clear()

def test_put_photos_dir_validations(client, tmp_path):
    app.dependency_overrides[api_settings.is_loopback_caller] = lambda: True
    
    response = client.put("/api/settings/photos-dir", json={"path": "  "})
    assert response.status_code == 422
    assert response.json()["detail"]["kind"] == "blank"
    
    response = client.put("/api/settings/photos-dir", json={"path": "relative/path"})
    assert response.status_code == 422
    assert response.json()["detail"]["kind"] == "not_absolute"
    
    response = client.put("/api/settings/photos-dir", json={"path": str(tmp_path / "nope")})
    assert response.status_code == 422
    assert response.json()["detail"]["kind"] == "not_found"
    
    (tmp_path / "file").touch()
    response = client.put("/api/settings/photos-dir", json={"path": str(tmp_path / "file")})
    assert response.status_code == 422
    assert response.json()["detail"]["kind"] == "not_a_dir"
    app.dependency_overrides.pop(api_settings.is_loopback_caller)

def test_put_photos_dir_empty_dir_success(client, tmp_path):
    app.dependency_overrides[api_settings.is_loopback_caller] = lambda: True
    
    (tmp_path / "empty").mkdir()
    response = client.put("/api/settings/photos-dir", json={"path": str(tmp_path / "empty")})
    assert response.status_code == 200
    assert response.json()["folder_count"] == 0
    assert response.json()["path"] == str(tmp_path / "empty")
    app.dependency_overrides.pop(api_settings.is_loopback_caller)

def test_put_photos_dir_invalidates_index(client, tmp_path):
    app.dependency_overrides[api_settings.is_loopback_caller] = lambda: True
    
    import backend.app.services.shipping_photos as sp
    from backend.app.services.shipping_photos import resolve_folder_index
    
    sp._cached_index = None
    
    (tmp_path / "old").mkdir()
    (tmp_path / "old" / "2023_01_01").mkdir()
    
    (tmp_path / "new").mkdir()
    (tmp_path / "new" / "2023_02_02").mkdir()
    
    app.dependency_overrides[get_settings] = lambda: Settings(shipping_photos_dir=str(tmp_path / "old"))
    
    idx1 = resolve_folder_index(Settings(shipping_photos_dir=str(tmp_path / "old")), lambda: 0)
    assert "2023_01_01" in idx1.folder_names
    
    response = client.put("/api/settings/photos-dir", json={"path": str(tmp_path / "new")})
    assert response.status_code == 200
    
    app.dependency_overrides[get_settings] = lambda: Settings(shipping_photos_dir=str(tmp_path / "old")) # actually not needed, store wins
    idx2 = resolve_folder_index(Settings(), lambda: 0)
    assert "2023_02_02" in idx2.folder_names
    assert "2023_01_01" not in idx2.folder_names
    app.dependency_overrides.clear()
    
def test_put_photos_dir_storage_failure(client, monkeypatch, tmp_path):
    app.dependency_overrides[api_settings.is_loopback_caller] = lambda: True
    
    def mock_save(*args, **kwargs):
        raise rc.RuntimeConfigWriteError("Failed")
        
    monkeypatch.setattr(api_settings, "save_photos_dir", mock_save)
    
    (tmp_path / "new").mkdir()
    response = client.put("/api/settings/photos-dir", json={"path": str(tmp_path / "new")})
    assert response.status_code == 500
    assert response.json()["detail"]["kind"] == "storage"
    app.dependency_overrides.pop(api_settings.is_loopback_caller)
