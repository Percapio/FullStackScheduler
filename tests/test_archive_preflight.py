"""Phase 32 §6.3, §7.2, §9 — snapshot, probing, identity, and the plan."""
import io
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.api import create_app
from backend.app.config import Settings, get_settings
from backend.app.services.archive_status import get_status
from backend.app.services.archive_zip import InlineContent
from backend.app.services.photo_files import (
    ROOT, Admitted, AdmittedFile, Excluded, FileIdentity, PhotoFileEntry, PhotoFileIndex,
    PhotoFileListStatus, build_archive_plan, build_snapshot, probe_entry,
    resolve_file_index, same_identity,
)

from .archive_harness import reset_archive_state


def entry(name: str, size: int = 1) -> PhotoFileEntry:
    return PhotoFileEntry(name=name, size_bytes=size, mtime_ns=0, version="v", previewable=True)


def index_of(names, truncated=False) -> PhotoFileIndex:
    entries = [entry(n) for n in names]
    return PhotoFileIndex(
        key=("2023_01_01", ROOT), status=PhotoFileListStatus.OK, entries=entries,
        by_name={e.name: e for e in entries}, folders=[], folder_set=set(), total_bytes=len(entries),
        scanned_at=0.0, truncated=truncated, folders_truncated=False,
    )


# ---- same_identity (§7.2) ---------------------------------------------------

def test_same_identity_compares_file_ids_only_when_both_present():
    a = FileIdentity(10, 5, (1, 2))
    assert same_identity(a, FileIdentity(10, 5, (1, 2)))
    assert not same_identity(a, FileIdentity(10, 5, (1, 3)))
    assert same_identity(a, FileIdentity(10, 5, None))
    assert same_identity(FileIdentity(10, 5, None), FileIdentity(10, 5, None))
    assert not same_identity(a, FileIdentity(11, 5, (1, 2)))
    assert not same_identity(a, FileIdentity(10, 6, (1, 2)))


# ---- V7 ---------------------------------------------------------------------

def test_v7_covers_full_listing_is_membership_not_count():
    index = index_of(["a.jpg", "b.jpg"], truncated=True)
    snapshot = build_snapshot(["a.jpg", "zzz.jpg"], index)
    assert snapshot.unresolved == ["zzz.jpg"]
    assert snapshot.covers_full_listing is False
    probes = [Admitted_for(e) for e in snapshot.entries]
    plan = build_archive_plan(snapshot, probes, 2)
    assert "_TRUNCATED.txt" not in [m.name.raw.decode() for m in plan.members]


def test_v7_full_selection_of_truncated_listing_emits_truncated():
    index = index_of(["a.jpg", "b.jpg"], truncated=True)
    snapshot = build_snapshot(["b.jpg", "a.jpg"], index)
    assert snapshot.covers_full_listing is True
    assert [e.name for e in snapshot.entries] == ["b.jpg", "a.jpg"]
    plan = build_archive_plan(snapshot, [Admitted_for(e) for e in snapshot.entries], 2)
    assert [m.name.raw.decode() for m in plan.members] == ["b.jpg", "a.jpg", "_TRUNCATED.txt"]


def Admitted_for(e: PhotoFileEntry):
    from backend.app.services.photo_files import ProbedEntry
    return Admitted(ProbedEntry(entry=e, resolved=Path(e.name), identity=FileIdentity(e.size_bytes, 0, None)))


# ---- V6, plan ordering ------------------------------------------------------

def test_v6_plans_are_reproducible_and_collision_named():
    index = index_of(["_MISSING.txt", "a.jpg"])
    snapshot = build_snapshot(["_MISSING.txt", "a.jpg", "gone.jpg"], index)
    probes = [Admitted_for(snapshot.entries[0]), Excluded("Unreadable")]
    first = build_archive_plan(snapshot, probes, 2000)
    second = build_archive_plan(snapshot, probes, 2000)
    assert first == second
    names = [m.name.raw.decode() for m in first.members]
    assert names == ["_MISSING.txt", "_MISSING (2).txt"]
    assert [(e.name, e.cause) for e in first.excluded] == [("gone.jpg", "Vanished"), ("a.jpg", "Unreadable")]
    manifest = first.members[-1].source
    assert isinstance(manifest, InlineContent)
    assert all(line.endswith("\n") for line in manifest.content.decode().splitlines(keepends=True))
    assert first.file_member_count == 1


# ---- probe_entry (V8 unit, V9, V9b, V10) ------------------------------------

@pytest.fixture
def folder(tmp_path):
    d = (tmp_path / "2023_01_01")
    d.mkdir()
    return d.resolve()


def test_probe_admits_a_regular_file_with_its_handle_identity(folder):
    (folder / "a.jpg").write_bytes(b"abc")
    result = probe_entry(folder, entry("a.jpg"))
    assert isinstance(result, Admitted)
    assert result.probed.identity.size == 3


def test_probe_excludes_vanished_and_directories(folder):
    assert probe_entry(folder, entry("gone.jpg")) == Excluded("Vanished")
    (folder / "sub").mkdir()
    assert probe_entry(folder, entry("sub")) == Excluded("NotRegularFile")


def test_probe_excludes_unreadable_on_open_failure(folder):
    (folder / "a.jpg").write_bytes(b"abc")

    def denied(path):
        raise PermissionError(13, "denied")

    assert probe_entry(folder, entry("a.jpg"), open_fn=denied) == Excluded("Unreadable")


def test_v9b_containment_without_symlink_privilege(folder, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_bytes(b"secret")
    (folder / "inside.jpg").write_bytes(b"ok")
    (folder / "link.jpg").write_bytes(b"placeholder")
    (folder / "inner_link.jpg").write_bytes(b"placeholder")

    def resolver(path: Path) -> Path:
        if path.name == "link.jpg":
            return outside.resolve()
        if path.name == "inner_link.jpg":
            return (folder / "inside.jpg").resolve()
        return path.resolve()

    assert probe_entry(folder, entry("link.jpg"), resolve=resolver) == Excluded("OutsideFolder")
    admitted = probe_entry(folder, entry("inner_link.jpg"), resolve=resolver)
    assert isinstance(admitted, Admitted)
    assert admitted.probed.resolved == (folder / "inside.jpg").resolve()


def _symlink_or_skip(link: Path, target: Path):
    try:
        os.symlink(target, link)
    except OSError as failure:
        pytest.skip(f"SeCreateSymbolicLinkPrivilege not held ({failure}); G6 requires this test to pass on the build machine")


def test_v9_live_symlink_containment(tmp_path, monkeypatch):
    settings = Settings(shipping_photos_dir=str(tmp_path))
    d = tmp_path / "2023_01_01"
    d.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_bytes(b"SECRET-BYTES-" * 100)
    (d / "inside.jpg").write_bytes(b"inside")
    _symlink_or_skip(d / "escape.jpg", outside)
    _symlink_or_skip(d / "alias.jpg", d / "inside.jpg")

    client = make_client(settings, monkeypatch)
    token = client.post("/api/photos/archive-token", json={"date_folder": "2023_01_01", "selection": []}).json()["token"]
    response = client.get("/api/photos/archive-download", params={"token": token})
    assert b"SECRET-BYTES-" not in response.content
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert "alias.jpg" in archive.namelist()
    assert "escape.jpg" not in archive.namelist()
    assert "escape.jpg — is not a regular file inside the photos folder" in archive.read("_MISSING.txt").decode()


def test_v10_benign_swap_is_reprobed_once(folder):
    target = folder / "a.jpg"
    target.write_bytes(b"old!")
    replacement = folder / "incoming.tmp"
    calls = []

    def swapping_stat(path):
        observed = os.stat(path)
        calls.append(path)
        if len(calls) == 1:
            time.sleep(0.01)
            replacement.write_bytes(b"new!")
            os.replace(replacement, target)
        return observed

    result = probe_entry(folder, entry("a.jpg"), stat_fn=swapping_stat)
    assert len(calls) == 2
    assert isinstance(result, Admitted)
    assert result.probed.identity == probe_entry(folder, entry("a.jpg")).probed.identity


def test_v10_persistent_swap_is_excluded(folder):
    target = folder / "a.jpg"
    target.write_bytes(b"v0")
    calls = []

    def always_swapping_stat(path):
        observed = os.stat(path)
        calls.append(path)
        time.sleep(0.01)
        staged = folder / f"incoming{len(calls)}.tmp"
        staged.write_bytes(f"v{len(calls)}".encode())
        os.replace(staged, target)
        return observed

    assert probe_entry(folder, entry("a.jpg"), stat_fn=always_swapping_stat) == Excluded("Unreadable")
    assert len(calls) == 2


# ---- V8 end to end, in-process ---------------------------------------------

def make_client(settings: Settings, monkeypatch) -> TestClient:
    reset_archive_state(monkeypatch)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


def _deny_read(path: Path):
    if sys.platform == "win32":
        subprocess.run(["icacls", str(path), "/deny", "*S-1-1-0:(RD)"], check=True, capture_output=True)
        return lambda: subprocess.run(["icacls", str(path), "/remove:d", "*S-1-1-0"], check=True, capture_output=True)
    os.chmod(path, 0)
    return lambda: os.chmod(path, 0o644)


def test_v8_preflight_exclusions_are_declared(tmp_path, monkeypatch):
    settings = Settings(shipping_photos_dir=str(tmp_path))
    d = tmp_path / "2023_01_01"
    d.mkdir()
    (d / "keep.jpg").write_bytes(b"k" * 1000)
    (d / "deleted.jpg").write_bytes(b"d" * 1000)
    (d / "denied.jpg").write_bytes(b"x" * 1000)
    client = make_client(settings, monkeypatch)
    resolve_file_index("2023_01_01", ROOT, settings, time.monotonic)
    token = client.post("/api/photos/archive-token", json={"date_folder": "2023_01_01", "selection": []}).json()["token"]

    (d / "deleted.jpg").unlink()
    restore = _deny_read(d / "denied.jpg")
    try:
        if sys.platform != "win32" and os.access(d / "denied.jpg", os.R_OK):
            pytest.skip("running with privileges that bypass file modes")
        response = client.get("/api/photos/archive-download", params={"token": token})
    finally:
        restore()

    assert response.status_code == 200
    assert int(response.headers["content-length"]) == len(response.content)
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert archive.namelist() == ["keep.jpg", "_MISSING.txt"]
    manifest = archive.read("_MISSING.txt").decode()
    assert "  deleted.jpg — moved or deleted before the archive was built\n" in manifest
    assert "  denied.jpg — could not be read from the photos folder\n" in manifest
    status = get_status(token, settings, time.monotonic)
    assert (status.outcome, status.entry_count, status.unresolved_count) == ("Completed", 1, 2)
    assert status.bytes_sent == len(response.content)
