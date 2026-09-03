import pytest
import os
import time
from pathlib import Path
from PIL import Image

from backend.app.config import Settings
from backend.app.services.photo_files import PhotoFileIndex, PhotoFileListStatus, PhotoFileEntry
from backend.app.services.photo_thumbnails import resolve_thumbnail, _get_cache_dir

@pytest.fixture
def mock_cache_dir(monkeypatch, tmp_path):
    cache_path = tmp_path / "cache"
    monkeypatch.setattr("backend.app.services.photo_thumbnails._get_cache_dir", lambda: cache_path)
    cache_path.mkdir()
    return cache_path

@pytest.fixture(autouse=True)
def mock_runtime_config(monkeypatch):
    import backend.app.services.runtime_config as rc
    rc._cached_config = None
    monkeypatch.setattr(rc, "load_runtime_config", lambda: {})

def test_resolve_thumbnail_success(tmp_path, mock_cache_dir):
    settings = Settings(
        shipping_photos_dir=str(tmp_path),
        shipping_photos_thumb_max_edge_px=100
    )
    (tmp_path / "2023_01_01").mkdir()
    
    # Create valid image
    img_path = tmp_path / "2023_01_01" / "valid.jpg"
    img = Image.new("RGB", (200, 200), color="red")
    img.save(img_path, format="JPEG")
    
    idx = PhotoFileIndex(
        status=PhotoFileListStatus.OK,
        entries=[],
        by_name={"valid.jpg": PhotoFileEntry("valid.jpg", 100, 100, "100-100", True)},
        total_bytes=100,
        scanned_at=0,
        truncated=False
    )
    
    res, result = resolve_thumbnail("2023_01_01", "valid.jpg", idx, settings)
    assert res == "ok"
    assert result.media_type == "image/webp"
    assert result.path.exists()
    
    # Check dimensions
    with Image.open(result.path) as t:
        assert t.size == (100, 100)

def test_resolve_thumbnail_exif_transpose(tmp_path, mock_cache_dir):
    settings = Settings(
        shipping_photos_dir=str(tmp_path),
        shipping_photos_thumb_max_edge_px=100
    )
    (tmp_path / "2023_01_01").mkdir()
    
    img_path = tmp_path / "2023_01_01" / "exif.jpg"
    img = Image.new("RGB", (200, 100), color="red")
    # EXIF orientation 6 (Rotate 90 CW)
    exif_data = img.getexif()
    exif_data[274] = 6 
    img.save(img_path, format="JPEG", exif=exif_data)
    
    idx = PhotoFileIndex(
        status=PhotoFileListStatus.OK,
        entries=[],
        by_name={"exif.jpg": PhotoFileEntry("exif.jpg", 100, 100, "100-100", True)},
        total_bytes=100,
        scanned_at=0,
        truncated=False
    )
    
    res, result = resolve_thumbnail("2023_01_01", "exif.jpg", idx, settings)
    assert res == "ok"
    
    with Image.open(result.path) as t:
        # Before transpose, it was 200x100.
        # Orientation 6 means the camera was rotated 90 deg.
        # Transpose will swap dimensions to 100x200.
        # Then thumbnail will fit to max_edge=100. So it becomes 50x100.
        assert t.size == (50, 100)

def test_resolve_thumbnail_cache_unavailable(tmp_path, mock_cache_dir, monkeypatch):
    settings = Settings(
        shipping_photos_dir=str(tmp_path),
        shipping_photos_thumb_max_edge_px=100
    )
    (tmp_path / "2023_01_01").mkdir()
    
    img_path = tmp_path / "2023_01_01" / "valid.jpg"
    img = Image.new("RGB", (200, 200), color="red")
    img.save(img_path, format="JPEG")
    
    idx = PhotoFileIndex(
        status=PhotoFileListStatus.OK,
        entries=[],
        by_name={"valid.jpg": PhotoFileEntry("valid.jpg", 100, 100, "100-100", True)},
        total_bytes=100,
        scanned_at=0,
        truncated=False
    )
    
    # Mock replace to raise OSError
    import pathlib
    original_replace = pathlib.Path.replace
    def mock_replace(self, target):
        if self.suffix.startswith(".tmp"):
            raise OSError("Mock disk full")
        return original_replace(self, target)
        
    monkeypatch.setattr(pathlib.Path, "replace", mock_replace)
    
    res, result = resolve_thumbnail("2023_01_01", "valid.jpg", idx, settings)
    assert res == "err"
    assert result == "cache_unavailable"
    
    # Ensure no sentinel was written
    cache_key = "2023_01_01_valid.jpg_100-100.webp"
    assert not (mock_cache_dir / cache_key).exists()

def test_resolve_thumbnail_undecodable(tmp_path, mock_cache_dir):
    settings = Settings(shipping_photos_dir=str(tmp_path))
    (tmp_path / "2023_01_01").mkdir()
    
    # Create bad image
    img_path = tmp_path / "2023_01_01" / "bad.jpg"
    img_path.write_bytes(b"not an image")
    
    idx = PhotoFileIndex(
        status=PhotoFileListStatus.OK,
        entries=[],
        by_name={"bad.jpg": PhotoFileEntry("bad.jpg", 12, 100, "100-12", True)},
        total_bytes=12,
        scanned_at=0,
        truncated=False
    )
    
    res, msg = resolve_thumbnail("2023_01_01", "bad.jpg", idx, settings)
    assert res == "err"
    assert msg == "not_previewable"
    
    # Sentinel should exist
    sentinel = mock_cache_dir / "2023_01_01_bad.jpg_100-12.webp"
    assert sentinel.exists()
    assert sentinel.stat().st_size == 0

def test_resolve_thumbnail_sweep(tmp_path, mock_cache_dir):
    settings = Settings(
        shipping_photos_dir=str(tmp_path),
        shipping_photos_thumb_sweep_every_n_writes=2,
        shipping_photos_thumb_cache_max_bytes=10
    )
    (tmp_path / "2023_01_01").mkdir()
    
    # We will just write some fake cache entries directly to test sweeping
    p1 = mock_cache_dir / "old.webp"
    p1.write_bytes(b"1234567890123") # 13 bytes
    
    # Change mtime so p1 is older
    os.utime(p1, (0, 0))
    
    # Now generate one thumbnail, which will trigger sweep since it's the 2nd write? No we need 2 writes
    import backend.app.services.photo_thumbnails as pt
    pt._write_counter = 1
    
    img_path = tmp_path / "2023_01_01" / "valid.jpg"
    img = Image.new("RGB", (10, 10), color="red")
    img.save(img_path, format="JPEG")
    
    idx = PhotoFileIndex(
        status=PhotoFileListStatus.OK,
        entries=[],
        by_name={"valid.jpg": PhotoFileEntry("valid.jpg", 100, 100, "100-100", True)},
        total_bytes=100,
        scanned_at=0,
        truncated=False
    )
    
    res, result = resolve_thumbnail("2023_01_01", "valid.jpg", idx, settings)
    assert res == "ok"
    
    # Sweep should have run, old should be deleted
    assert not p1.exists()
