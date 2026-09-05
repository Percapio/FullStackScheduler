with open('tests/test_photo_thumbnails.py', 'r') as f:
    content = f.read()

content = content.replace('sentinel = mock_cache_dir / "2023_01_01_bad.jpg_100-12.webp"', 'from backend.app.services.photo_thumbnails import thumbnail_cache_key\n    sentinel = thumbnail_cache_key("2023_01_01", ROOT, "bad.jpg", "100-12", settings)')

with open('tests/test_photo_thumbnails.py', 'w') as f:
    f.write(content)
