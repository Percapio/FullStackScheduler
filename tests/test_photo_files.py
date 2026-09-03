import pytest
import os
import zipfile
import io
import time
from pathlib import Path

from backend.app.config import Settings
from backend.app.services.photo_files import (
    is_plausible_file_name,
    resolve_photo_file_path,
    PhotoFileIndex,
    PhotoFileListStatus,
    PhotoFileEntry,
    resolve_file_index,
    invalidate_file_index,
    ALL_FOLDERS,
    stream_photo_archive,
    FileStatus,
    _file_indexes
)
import backend.app.services.runtime_config as rc

@pytest.fixture(autouse=True)
def mock_runtime_config(monkeypatch):
    rc._cached_config = None
    monkeypatch.setattr(rc, "load_runtime_config", lambda: {})
    # _file_indexes is module-level and keyed by folder name alone. Other test
    # modules build a "2023_01_01" under their own tmp_path, so without this
    # the caching assertions below pass or fail on collection order.
    _file_indexes.clear()

def test_membership_guard_prefilter():
    assert is_plausible_file_name("spaces allowed.jpg") is True
    assert is_plausible_file_name("parens(1).jpg") is True
    assert is_plausible_file_name("hash#tag.jpg") is True
    assert is_plausible_file_name("plus+sign.jpg") is True
    assert is_plausible_file_name("percent%20.jpg") is True
    assert is_plausible_file_name("A" * 255) is True
    
    assert is_plausible_file_name("..") is True # the guard stops it later
    assert is_plausible_file_name(".") is True
    assert is_plausible_file_name("a/b.jpg") is False
    assert is_plausible_file_name("a\\b.jpg") is False
    assert is_plausible_file_name("") is False
    assert is_plausible_file_name("\x00.jpg") is False
    assert is_plausible_file_name("A" * 256) is False

def test_membership_guard_resolution(tmp_path):
    settings = Settings(shipping_photos_dir=str(tmp_path))
    (tmp_path / "2023_01_01").mkdir()
    (tmp_path / "2023_01_01" / "file.jpg").touch()
    
    idx = PhotoFileIndex(
        status=PhotoFileListStatus.OK,
        entries=[],
        by_name={"file.jpg": PhotoFileEntry("file.jpg", 0, 0, "0", True)},
        total_bytes=0,
        scanned_at=0,
        truncated=False
    )
    
    # Member
    res, path = resolve_photo_file_path("2023_01_01", "file.jpg", idx, settings)
    assert res == "ok"
    
    # Not member
    res, msg = resolve_photo_file_path("2023_01_01", "FILE.JPG", idx, settings)
    assert res == "err"
    assert msg == "file_not_found"
    
    # Pre-filter fail
    res, msg = resolve_photo_file_path("2023_01_01", "a/b", idx, settings)
    assert res == "err"
    
    # Folder not found
    res, msg = resolve_photo_file_path("2023_01_02", "file.jpg", idx, settings)
    assert res == "err"
    assert msg == "file_not_found"

def test_membership_guard_containment(tmp_path):
    settings = Settings(shipping_photos_dir=str(tmp_path))
    idx = PhotoFileIndex(
        status=PhotoFileListStatus.OK,
        entries=[],
        by_name={"file.jpg": PhotoFileEntry("file.jpg", 0, 0, "0", True)},
        total_bytes=0,
        scanned_at=0,
        truncated=False
    )
    
    (tmp_path / "2023_01_01").mkdir()
    
    # Inject resolve outside tree
    def evil_resolve(p):
        if p.name == "file.jpg":
            return (tmp_path / "other" / "file.jpg").resolve()
        return p.resolve()
        
    res, msg = resolve_photo_file_path("2023_01_01", "file.jpg", idx, settings, resolve=evil_resolve)
    assert res == "err"
    assert msg == "not_a_file"
    
    # Directory
    def dir_stat(p):
        return FileStatus(is_regular_file=False, size_bytes=0, mtime_ns=0)
        
    res, msg = resolve_photo_file_path("2023_01_01", "file.jpg", idx, settings, stat_fn=dir_stat)
    assert res == "err"
    assert msg == "not_a_file"

def test_file_index_caching(tmp_path):
    settings = Settings(
        shipping_photos_dir=str(tmp_path),
        shipping_photos_file_index_ttl_seconds=10.0,
        shipping_photos_file_unavailable_ttl_seconds=5.0
    )
    (tmp_path / "2023_01_01").mkdir()
    (tmp_path / "2023_01_01" / "f1.jpg").touch()
    
    t = 0.0
    
    idx1 = resolve_file_index("2023_01_01", settings, lambda: t)
    assert idx1.status == "ok"
    assert len(idx1.entries) == 1
    
    (tmp_path / "2023_01_01" / "f2.jpg").touch()
    
    # Under TTL, gets cached
    t = 5.0
    idx2 = resolve_file_index("2023_01_01", settings, lambda: t)
    assert len(idx2.entries) == 1
    
    # Over TTL, rescans
    t = 11.0
    idx3 = resolve_file_index("2023_01_01", settings, lambda: t)
    assert len(idx3.entries) == 2
    
def test_file_index_truncation_and_size(tmp_path):
    settings = Settings(
        shipping_photos_dir=str(tmp_path),
        shipping_photos_max_files_per_folder=2
    )
    (tmp_path / "2023_01_01").mkdir()
    
    for i in range(5):
        p = tmp_path / "2023_01_01" / f"f{i}.jpg"
        p.write_bytes(b"x") # 1 byte
        
    idx = resolve_file_index("2023_01_01", settings, time.time)
    assert idx.truncated is True
    assert len(idx.entries) == 2
    assert idx.total_bytes == 2

def test_file_index_lru(tmp_path):
    settings = Settings(
        shipping_photos_dir=str(tmp_path),
        shipping_photos_file_index_max_folders=2
    )
    (tmp_path / "2023_01_01").mkdir()
    (tmp_path / "2023_01_02").mkdir()
    (tmp_path / "2023_01_03").mkdir()
    
    resolve_file_index("2023_01_01", settings, time.time)
    resolve_file_index("2023_01_02", settings, time.time)
    
    # Touch 1
    resolve_file_index("2023_01_01", settings, time.time)
    
    # Add 3
    resolve_file_index("2023_01_03", settings, time.time)
    
    # 2 should be evicted, 1 should remain
    assert "2023_01_02" not in _file_indexes
    assert "2023_01_01" in _file_indexes

def test_file_index_invalidate(tmp_path):
    settings = Settings(shipping_photos_dir=str(tmp_path))
    (tmp_path / "2023_01_01").mkdir()
    resolve_file_index("2023_01_01", settings, time.time)
    assert len(_file_indexes) > 0
    invalidate_file_index(ALL_FOLDERS())
    assert len(_file_indexes) == 0

def test_archive_streaming(tmp_path):
    settings = Settings(
        shipping_photos_dir=str(tmp_path),
        shipping_photos_max_files_per_folder=2
    )
    d = tmp_path / "2023_01_01"
    d.mkdir()
    (d / "a.jpg").write_bytes(b"a")
    (d / "b.jpg").write_bytes(b"b")
    (d / "c.notpreviewable").write_bytes(b"c") # should be archived if in index
    
    idx = resolve_file_index("2023_01_01", settings, time.time)
    assert len(idx.entries) == 2
    assert idx.truncated is True
    
    stream = stream_photo_archive("2023_01_01", [], idx, settings)
    data = b"".join(stream)
    
    assert len(data) > 0
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = zf.namelist()
    assert "a.jpg" in names
    assert "b.jpg" in names
    assert "_TRUNCATED.txt" in names

def test_archive_streaming_missing_file(tmp_path):
    settings = Settings(shipping_photos_dir=str(tmp_path))
    d = tmp_path / "2023_01_01"
    d.mkdir()
    (d / "a.jpg").write_bytes(b"a")
    
    idx = resolve_file_index("2023_01_01", settings, time.time)
    
    (d / "a.jpg").unlink() # Delete before streaming
    
    stream = stream_photo_archive("2023_01_01", [], idx, settings)
    data = b"".join(stream)
    zf = zipfile.ZipFile(io.BytesIO(data))
    assert "_MISSING.txt" in zf.namelist()
