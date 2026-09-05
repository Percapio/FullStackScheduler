import re

with open('tests/test_photo_thumbnails.py', 'r') as f:
    content = f.read()

content = re.sub(r'resolve_thumbnail\("([0-9_]+)", "([^"]+\.jpg)"', r'resolve_thumbnail("\1", ROOT, "\2"', content)
content = re.sub(r'resolve_thumbnail\("([0-9_]+)", "a/b"', r'resolve_thumbnail("\1", ROOT, "a/b"', content)

with open('tests/test_photo_thumbnails.py', 'w') as f:
    f.write(content)

with open('tests/test_photo_warm.py', 'r') as f:
    content = f.read()

content = re.sub(r'def worker\(\):', r'def worker():\n    from backend.app.services.photo_files import ROOT', content)

with open('tests/test_photo_warm.py', 'w') as f:
    f.write(content)
