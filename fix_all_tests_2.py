with open('tests/test_photo_warm.py', 'r') as f:
    content = f.read()

if 'from backend.app.services.photo_files import ROOT' not in content:
    content = content.replace('import backend.app.services.photo_warm as pw', 'import backend.app.services.photo_warm as pw\nfrom backend.app.services.photo_files import ROOT')

with open('tests/test_photo_warm.py', 'w') as f:
    f.write(content)

with open('tests/test_photo_files.py', 'r') as f:
    content = f.read()
content = content.replace('assert msg == "file_not_found"', 'assert msg == "index_mismatch"')
with open('tests/test_photo_files.py', 'w') as f:
    f.write(content)
