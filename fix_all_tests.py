import os
import re

def fix_photo_files():
    with open('tests/test_photo_files.py', 'r') as f:
        content = f.read()

    content = content.replace('assert msg == "file_not_found"', 'assert msg == "index_mismatch"')
    content = content.replace('shipping_photos_file_index_max_folders=2', 'shipping_photos_file_index_max_keys=2')
    content = content.replace('assert "2023_01_02" not in _file_indexes', 'assert ("2023_01_02", ROOT) not in _file_indexes')
    content = content.replace('assert "2023_01_01" in _file_indexes', 'assert ("2023_01_01", ROOT) in _file_indexes')
    content = content.replace('assert "2023_01_01" not in _file_indexes', 'assert ("2023_01_01", ROOT) not in _file_indexes')
    content = content.replace('assert "2023_01_03" not in _file_indexes', 'assert ("2023_01_03", ROOT) not in _file_indexes')

    with open('tests/test_photo_files.py', 'w') as f:
        f.write(content)

def fix_photo_thumbnails():
    with open('tests/test_photo_thumbnails.py', 'r') as f:
        content = f.read()

    # Re-verify ROOT import
    if 'from backend.app.services.photo_files import' in content and 'ROOT' not in content:
        content = content.replace('from backend.app.services.photo_files import PhotoFileIndex, PhotoFileListStatus, PhotoFileEntry', 'from backend.app.services.photo_files import PhotoFileIndex, PhotoFileListStatus, PhotoFileEntry, ROOT')
        
    # generate_once might be missing ROOT.
    # regex to find generate_once("YYYY_MM_DD", "filename"
    content = re.sub(r'generate_once\("([0-9_]+)", "([^"]+\.jpg)"', r'generate_once("\1", ROOT, "\2"', content)
    content = re.sub(r'generate_once\("([0-9_]+)", "a/b"', r'generate_once("\1", ROOT, "a/b"', content)

    # _one_file_index(name: str) -> PhotoFileIndex
    # ensure it uses key=("2023_01_01", ROOT) etc.
    # We already replaced PhotoFileIndex() using the first script, but let's double check.

    with open('tests/test_photo_thumbnails.py', 'w') as f:
        f.write(content)

def fix_photo_warm():
    with open('tests/test_photo_warm.py', 'r') as f:
        content = f.read()

    if 'ROOT' not in content:
        content = content.replace('import backend.app.services.photo_warm as pw', 'import backend.app.services.photo_warm as pw\nfrom backend.app.services.photo_files import ROOT')

    # Find pw.enqueue_warm("...", settings) -> pw.enqueue_warm("...", ROOT, settings)
    content = re.sub(r'pw\.enqueue_warm\(([^,]+),\s*settings\)', r'pw.enqueue_warm(\1, ROOT, settings)', content)

    # Check for assert list(pw._warm_queue) == ["..."]
    # We replaced some, but maybe not all. Let's do a regex for any string in the list
    content = re.sub(r'\["([0-9_]+)"\]', r'[("\1", ROOT)]', content)
    content = re.sub(r'\["([0-9_]+)", "([0-9_]+)"\]', r'[("\1", ROOT), ("\2", ROOT)]', content)
    content = re.sub(r'\["f7", "f8"\]', r'[("f7", ROOT), ("f8", ROOT)]', content)
    
    content = re.sub(r'assert "([0-9_]+)" in pw\._warm_known', r'assert ("\1", ROOT) in pw._warm_known', content)

    with open('tests/test_photo_warm.py', 'w') as f:
        f.write(content)

fix_photo_files()
fix_photo_thumbnails()
fix_photo_warm()
