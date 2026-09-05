with open('src/api/__tests__/photos.spec.ts', 'r') as f:
    content = f.read()

content = content.replace('requestArchiveTicket("2023_01_01", [])', 'requestArchiveTicket("2023_01_01", "", [])')
content = content.replace('requestArchiveTicket("2023_01_01", ["a.jpg", "b.jpg"])', 'requestArchiveTicket("2023_01_01", "", ["a.jpg", "b.jpg"])')

with open('src/api/__tests__/photos.spec.ts', 'w') as f:
    f.write(content)

with open('src/composables/__tests__/usePhotoGallery.spec.ts', 'r') as f:
    content = f.read()

content = content.replace(
    'entries: [\n                { name: "a.jpg", size_bytes: 10, mtime_ns: 1, version: "v1", previewable: true },\n                { name: "b.jpg", size_bytes: 10, mtime_ns: 1, version: "v1", previewable: true }\n            ],\n            truncated: false',
    'folders: [],\n            folders_truncated: false,\n            entries: [\n                { name: "a.jpg", size_bytes: 10, mtime_ns: 1, version: "v1", previewable: true },\n                { name: "b.jpg", size_bytes: 10, mtime_ns: 1, version: "v1", previewable: true }\n            ],\n            truncated: false'
)

content = content.replace(
    'entries: [],\n            truncated: false',
    'folders: [],\n            folders_truncated: false,\n            entries: [],\n            truncated: false'
)

with open('src/composables/__tests__/usePhotoGallery.spec.ts', 'w') as f:
    f.write(content)
