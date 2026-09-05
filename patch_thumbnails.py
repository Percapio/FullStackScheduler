with open("backend/app/services/photo_thumbnails.py", "r") as f:
    content = f.read()

content = content.replace(
    """def generate_once(
    date_folder: str,
    file_name: str,""",
    """def thumbnail_cache_key(date_folder: str, sub_folder: str, file_name: str, version: str) -> str:
    import hashlib
    raw = f"{date_folder}\\x00{sub_folder}\\x00{file_name}\\x00{version}".encode('utf-8')
    return hashlib.sha256(raw).hexdigest()[:32] + ".webp"

def generate_once(
    date_folder: str,
    sub_folder: str,
    file_name: str,"""
)

content = content.replace(
    """res = resolve_photo_file_path(date_folder, file_name, index, settings)""",
    """res = resolve_photo_file_path(date_folder, sub_folder, file_name, index, settings)"""
)

content = content.replace(
    """cache_key = f"{date_folder}_{file_name}_{entry.version}.webp".replace("\\\\", "_").replace("/", "_")""",
    """cache_key = thumbnail_cache_key(date_folder, sub_folder, file_name, entry.version)"""
)

with open("backend/app/services/photo_thumbnails.py", "w") as f:
    f.write(content)
