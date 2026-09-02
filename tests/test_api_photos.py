import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.config import get_settings, Settings
from backend.app.services.shipping_photos import RateLimited
import backend.app.services.shipping_photos as sp

client = TestClient(app)

import backend.app.services.runtime_config as rc

@pytest.fixture
def override_settings(monkeypatch, tmp_path):
    rc._cached_config = None
    monkeypatch.setattr(rc, "_runtime_root", lambda: tmp_path)
    
    settings = Settings(
        shipping_photos_dir=str(tmp_path),
        shipping_photos_open_min_interval_seconds=0.0
    )
    app.dependency_overrides[get_settings] = lambda: settings
    yield settings
    app.dependency_overrides.clear()

@pytest.fixture
def reset_module_state():
    sp._cached_index = None
    sp._last_open_monotonic = None
    yield
    sp._cached_index = None
    sp._last_open_monotonic = None

def test_get_available_dates_ok(override_settings, tmp_path, reset_module_state):
    (tmp_path / "2023_07_25").mkdir()
    (tmp_path / "2023_07_24").mkdir()
    
    # Assert folders is sorted ascending
    response = client.get("/api/photos/available-dates")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "folders": ["2023_07_24", "2023_07_25"],
        "truncated": False
    }

def test_get_available_dates_unconfigured(override_settings, reset_module_state):
    override_settings.shipping_photos_dir = ""
    
    response = client.get("/api/photos/available-dates")
    assert response.status_code == 200
    assert response.json() == {
        "status": "unconfigured",
        "folders": [],
        "truncated": False
    }

def test_get_available_dates_unavailable(override_settings, tmp_path, reset_module_state):
    override_settings.shipping_photos_dir = str(tmp_path / "nonexistent")
    
    response = client.get("/api/photos/available-dates")
    assert response.status_code == 200
    assert response.json() == {
        "status": "unavailable",
        "folders": [],
        "truncated": False
    }

def test_get_available_dates_probe_ignored_if_malformed(override_settings, tmp_path, reset_module_state):
    response = client.get("/api/photos/available-dates?probe=invalid&probe=2023_07_24")
    assert response.status_code == 200
    # The malformed one is ignored, not 422'd

def test_open_photo_folder_malformed_422(override_settings, monkeypatch):
    # Pydantic 422 before service
    service_called = False
    def mock_open(*args):
        nonlocal service_called
        service_called = True
        return ("err", "shell_error")
    monkeypatch.setattr(sp, "open_photo_folder", mock_open)
    
    response = client.post("/api/photos/open", json={"date_folder": "invalid"})
    assert response.status_code == 422
    assert not service_called

def test_open_photo_folder_ok(override_settings, monkeypatch):
    def mock_open(*args):
        return ("ok", "2023_07_24")
    monkeypatch.setattr("backend.app.api.photos.open_photo_folder", mock_open)
    
    response = client.post("/api/photos/open", json={"date_folder": "2023_07_24"})
    assert response.status_code == 200
    assert response.json() == {"opened": "2023_07_24"}

def test_open_photo_folder_failures(override_settings, monkeypatch):
    cases = [
        ("unconfigured", 409, {"kind": "unconfigured"}),
        ("unavailable", 409, {"kind": "unavailable"}),
        ("not_found", 404, {"kind": "not_found", "date_folder": "2023_07_24"}),
        ("shell_error", 500, {"kind": "shell_error"}),
    ]
    
    for failure, expected_status, expected_body in cases:
        def mock_open(*args, fail=failure):
            return ("err", fail)
        monkeypatch.setattr("backend.app.api.photos.open_photo_folder", mock_open)
        
        response = client.post("/api/photos/open", json={"date_folder": "2023_07_24"})
        assert response.status_code == expected_status
        assert response.json() == expected_body
        assert str(override_settings.shipping_photos_dir) not in str(response.json())

def test_open_photo_folder_rate_limited(override_settings, monkeypatch):
    def mock_open(*args):
        # 1.1 ceil to 2
        return ("err", RateLimited(remaining_seconds=1.1))
    monkeypatch.setattr("backend.app.api.photos.open_photo_folder", mock_open)
    
    response = client.post("/api/photos/open", json={"date_folder": "2023_07_24"})
    assert response.status_code == 429
    assert response.json() == {"kind": "rate_limited", "retry_after_seconds": 2}
    assert response.headers["Retry-After"] == "2"

def test_open_photo_folder_rate_limited_floor(override_settings, monkeypatch):
    def mock_open(*args):
        # 0.1 ceil to 1
        return ("err", RateLimited(remaining_seconds=0.1))
    monkeypatch.setattr("backend.app.api.photos.open_photo_folder", mock_open)
    
    response = client.post("/api/photos/open", json={"date_folder": "2023_07_24"})
    assert response.status_code == 429
    assert response.json() == {"kind": "rate_limited", "retry_after_seconds": 1}
    assert response.headers["Retry-After"] == "1"
