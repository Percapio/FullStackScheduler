with open('tests/test_api_photos.py', 'r') as f:
    content = f.read()
content = content.replace('def mock_enqueue(folder, settings):', 'def mock_enqueue(folder, sub_folder, settings):')
content = content.replace('ticket = ArchiveTicket("2023_01_01", [], "file.zip", False, issued_at=999.0)', 'ticket = ArchiveTicket("2023_01_01", "", [], "file.zip", False, issued_at=999.0)')
with open('tests/test_api_photos.py', 'w') as f:
    f.write(content)
