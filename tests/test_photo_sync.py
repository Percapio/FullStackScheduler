import pytest
from backend.app.services.photo_sync import get_photo_sync_state, _save_photo_sync_state
from backend.app.services.runtime_config import _runtime_root

def test_photo_sync_state(tmp_path, monkeypatch):
    import backend.app.services.photo_sync as ps
    monkeypatch.setattr(ps, "_runtime_root", lambda: tmp_path)
    
    # Check default
    import backend.app.services.photo_sync as ps
    ps._photo_sync_state_cache = None
    state = get_photo_sync_state()
    assert state["last_run_outcome"] is None
    
    # Save and read back
    state["last_run_outcome"] = "Failed"
    state["last_run_files_copied"] = 42
    _save_photo_sync_state(state)
    
    state2 = get_photo_sync_state()
    assert state2["last_run_outcome"] == "Failed"
    assert state2["last_run_files_copied"] == 42
