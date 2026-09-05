def patch_file(path, replacements):
    with open(path, 'r') as f:
        content = f.read()
    for old, new in replacements:
        content = content.replace(old, new)
    with open(path, 'w') as f:
        f.write(content)

patch_file('tests/test_photo_files.py', [
    ('from backend.app.services.photo_files import (', 'from backend.app.services.photo_files import ( ROOT,'),
    ('resolve_file_index("2023_01_01", settings', 'resolve_file_index("2023_01_01", ROOT, settings'),
    ('resolve_file_index("2023_01_02", settings', 'resolve_file_index("2023_01_02", ROOT, settings'),
    ('resolve_file_index("2023_01_03", settings', 'resolve_file_index("2023_01_03", ROOT, settings'),
    ('resolve_photo_file_path("2023_01_01", "file.jpg", idx', 'resolve_photo_file_path("2023_01_01", ROOT, "file.jpg", idx'),
    ('resolve_photo_file_path("2023_01_01", "FILE.JPG", idx', 'resolve_photo_file_path("2023_01_01", ROOT, "FILE.JPG", idx'),
    ('resolve_photo_file_path("2023_01_01", "a/b", idx', 'resolve_photo_file_path("2023_01_01", ROOT, "a/b", idx'),
    ('resolve_photo_file_path("2023_01_02", "file.jpg", idx', 'resolve_photo_file_path("2023_01_02", ROOT, "file.jpg", idx'),
    ('stream_photo_archive("2023_01_01", ', 'stream_photo_archive("2023_01_01", ROOT, '),
    ('idx = PhotoFileIndex(\n        status=PhotoFileListStatus.OK,\n        entries=[e],\n        by_name={"file.jpg": e},\n        total_bytes=100,\n        scanned_at=100.0,\n        truncated=False\n    )', 'idx = PhotoFileIndex(\n        key=("2023_01_01", ROOT),\n        status=PhotoFileListStatus.OK,\n        entries=[e],\n        by_name={"file.jpg": e},\n        folders=[],\n        folder_set=set(),\n        total_bytes=100,\n        scanned_at=100.0,\n        truncated=False,\n        folders_truncated=False\n    )'),
    ('idx = PhotoFileIndex(\n        status=PhotoFileListStatus.OK,\n        entries=[e1, e2],\n        by_name={e1.name: e1, e2.name: e2},\n        total_bytes=300,\n        scanned_at=100.0,\n        truncated=False\n    )', 'idx = PhotoFileIndex(\n        key=("2023_01_01", ROOT),\n        status=PhotoFileListStatus.OK,\n        entries=[e1, e2],\n        by_name={e1.name: e1, e2.name: e2},\n        folders=[],\n        folder_set=set(),\n        total_bytes=300,\n        scanned_at=100.0,\n        truncated=False,\n        folders_truncated=False\n    )'),
    ('idx = PhotoFileIndex(\n        status=PhotoFileListStatus.OK,\n        entries=[e],\n        by_name={"file.jpg": e},\n        total_bytes=100,\n        scanned_at=100.0,\n        truncated=True\n    )', 'idx = PhotoFileIndex(\n        key=("2023_01_01", ROOT),\n        status=PhotoFileListStatus.OK,\n        entries=[e],\n        by_name={"file.jpg": e},\n        folders=[],\n        folder_set=set(),\n        total_bytes=100,\n        scanned_at=100.0,\n        truncated=True,\n        folders_truncated=False\n    )'),
    ('assert list(_file_indexes.keys()) == ["2023_01_02", "2023_01_01"]', 'assert list(_file_indexes.keys()) == [("2023_01_02", ROOT), ("2023_01_01", ROOT)]'),
    ('assert "2023_01_01" not in _file_indexes\n    assert "2023_01_02" not in _file_indexes\n    assert "2023_01_03" not in _file_indexes', 'assert ("2023_01_01", ROOT) not in _file_indexes\n    assert ("2023_01_02", ROOT) not in _file_indexes\n    assert ("2023_01_03", ROOT) not in _file_indexes'),
    ('idx = PhotoFileIndex(\n        status=PhotoFileListStatus.OK,\n        entries=[],\n        by_name={"file.jpg": PhotoFileEntry("file.jpg", 0, 0, "0", True)},\n        total_bytes=0,\n        scanned_at=0,\n        truncated=False\n    )', 'idx = PhotoFileIndex(\n        key=("2023_01_01", ROOT),\n        status=PhotoFileListStatus.OK,\n        entries=[],\n        by_name={"file.jpg": PhotoFileEntry("file.jpg", 0, 0, "0", True)},\n        folders=[],\n        folder_set=set(),\n        total_bytes=0,\n        scanned_at=0,\n        truncated=False,\n        folders_truncated=False\n    )')
])

patch_file('tests/test_photo_thumbnails.py', [
    ('from backend.app.services.photo_files import PhotoFileIndex, PhotoFileListStatus, PhotoFileEntry', 'from backend.app.services.photo_files import PhotoFileIndex, PhotoFileListStatus, PhotoFileEntry, ROOT'),
    ('idx = PhotoFileIndex(\n        status=PhotoFileListStatus.OK,\n        entries=[e],\n        by_name={"file.jpg": e},\n        total_bytes=100,\n        scanned_at=100.0,\n        truncated=False\n    )', 'idx = PhotoFileIndex(\n        key=("2023_01_01", ROOT),\n        status=PhotoFileListStatus.OK,\n        entries=[e],\n        by_name={"file.jpg": e},\n        folders=[],\n        folder_set=set(),\n        total_bytes=100,\n        scanned_at=100.0,\n        truncated=False,\n        folders_truncated=False\n    )'),
    ('generate_once("2023_01_01", "file.jpg", idx', 'generate_once("2023_01_01", ROOT, "file.jpg", idx'),
    ('generate_once("2023_01_01", "a/b", idx', 'generate_once("2023_01_01", ROOT, "a/b", idx'),
    ('generate_once("2023_01_02", "file.jpg", idx', 'generate_once("2023_01_02", ROOT, "file.jpg", idx'),
    ('idx = PhotoFileIndex(\n        status=PhotoFileListStatus.OK,\n        entries=[],\n        by_name={"to.jpg": PhotoFileEntry("to.jpg", 100, 100, "100-100", True)},\n        total_bytes=100,\n        scanned_at=0,\n        truncated=False\n    )', 'idx = PhotoFileIndex(\n        key=("2023_01_01", ROOT),\n        status=PhotoFileListStatus.OK,\n        entries=[],\n        by_name={"to.jpg": PhotoFileEntry("to.jpg", 100, 100, "100-100", True)},\n        folders=[],\n        folder_set=set(),\n        total_bytes=100,\n        scanned_at=0,\n        truncated=False,\n        folders_truncated=False\n    )'),
    ('idx = PhotoFileIndex(\n        status=PhotoFileListStatus.OK,\n        entries=[],\n        by_name={\n            "k1.jpg": PhotoFileEntry("k1.jpg", 100, 100, "100-100", True),\n            "k2.jpg": PhotoFileEntry("k2.jpg", 100, 100, "100-100", True),\n        },\n        total_bytes=200,\n        scanned_at=0,\n        truncated=False\n    )', 'idx = PhotoFileIndex(\n        key=("2023_01_01", ROOT),\n        status=PhotoFileListStatus.OK,\n        entries=[],\n        by_name={\n            "k1.jpg": PhotoFileEntry("k1.jpg", 100, 100, "100-100", True),\n            "k2.jpg": PhotoFileEntry("k2.jpg", 100, 100, "100-100", True),\n        },\n        folders=[],\n        folder_set=set(),\n        total_bytes=200,\n        scanned_at=0,\n        truncated=False,\n        folders_truncated=False\n    )'),
    ('return PhotoFileIndex(\n        status=PhotoFileListStatus.OK,\n        entries=[],\n        by_name={name: PhotoFileEntry(name, 100, 100, "100-100", True)},\n        total_bytes=100,\n        scanned_at=0,\n        truncated=False\n    )', 'return PhotoFileIndex(\n        key=("2023_01_01", ROOT),\n        status=PhotoFileListStatus.OK,\n        entries=[],\n        by_name={name: PhotoFileEntry(name, 100, 100, "100-100", True)},\n        folders=[],\n        folder_set=set(),\n        total_bytes=100,\n        scanned_at=0,\n        truncated=False,\n        folders_truncated=False\n    )')
])

patch_file('tests/test_photo_warm.py', [
    ('from backend.app.services.photo_warm import enqueue_warm, shutdown_warm_worker, _warm_queue, _warm_known, _warm_lock, warm_worker_loop', 'from backend.app.services.photo_warm import enqueue_warm, shutdown_warm_worker, _warm_queue, _warm_known, _warm_lock, warm_worker_loop\nfrom backend.app.services.photo_files import ROOT'),
    ('pw.enqueue_warm(f"f{i}", settings)', 'pw.enqueue_warm(f"f{i}", ROOT, settings)'),
    ('pw.enqueue_warm("2023_01_01", settings)', 'pw.enqueue_warm("2023_01_01", ROOT, settings)'),
    ('pw.enqueue_warm("2023_01_02", settings)', 'pw.enqueue_warm("2023_01_02", ROOT, settings)'),
    ('assert list(pw._warm_queue) == ["2023_01_01"]', 'assert list(pw._warm_queue) == [("2023_01_01", ROOT)]'),
    ('assert list(pw._warm_queue) == ["2023_01_01", "2023_01_02"]', 'assert list(pw._warm_queue) == [("2023_01_01", ROOT), ("2023_01_02", ROOT)]'),
    ('assert "2023_01_01" in pw._warm_known', 'assert ("2023_01_01", ROOT) in pw._warm_known'),
    ('assert "2023_01_02" in pw._warm_known', 'assert ("2023_01_02", ROOT) in pw._warm_known'),
    ('assert list(pw._warm_queue) == ["f7", "f8"]', 'assert list(pw._warm_queue) == [("f7", ROOT), ("f8", ROOT)]'),
])
