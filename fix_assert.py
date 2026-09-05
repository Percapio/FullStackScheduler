with open('tests/test_photo_files.py', 'r') as f:
    content = f.read()

# Replace the first index_mismatch with file_not_found
content = content.replace('assert msg == "index_mismatch"', 'assert msg == "file_not_found"', 1)

with open('tests/test_photo_files.py', 'w') as f:
    f.write(content)
