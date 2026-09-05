with open('tests/test_photo_thumbnails.py', 'r') as f:
    content = f.read()

content = content.replace('sentinel = thumbnail_cache_key("2023_01_01", ROOT, "bad.jpg", "100-12", settings)', 'sentinel = mock_cache_dir / thumbnail_cache_key("2023_01_01", ROOT, "bad.jpg", "100-12")')

with open('tests/test_photo_thumbnails.py', 'w') as f:
    f.write(content)
