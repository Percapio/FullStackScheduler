import re

with open('src/api/__tests__/photos.spec.ts', 'r') as f:
    content = f.read()

content = re.sub(r"requestArchiveTicket\('2023_01_01', \[\]\)", "requestArchiveTicket('2023_01_01', '', [])", content)
content = re.sub(r'requestArchiveTicket\("2023_01_01", \[\]\)', 'requestArchiveTicket("2023_01_01", "", [])', content)

with open('src/api/__tests__/photos.spec.ts', 'w') as f:
    f.write(content)

with open('src/composables/__tests__/usePhotoGallery.spec.ts', 'r') as f:
    content = f.read()

content = re.sub(r'entries: (\[.*?\]|\[\]),\s*truncated: (false|true)', r'folders: [], folders_truncated: false, entries: \1, truncated: \2', content, flags=re.DOTALL)

with open('src/composables/__tests__/usePhotoGallery.spec.ts', 'w') as f:
    f.write(content)
