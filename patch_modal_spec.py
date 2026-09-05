import re

with open("frontend/src/components/__tests__/PhotoGalleryModal.spec.ts", "r") as f:
    content = f.read()

content = content.replace(
    """    const state = ref<any>({
        state: 'ready',
        date_folder,
        entries,
        truncated: false,
        selection: new Set<string>()
    })""",
    """    const state = ref<any>({
        state: 'ready',
        date_folder,
        sub_folder: '',
        folders: [],
        entries,
        truncated: false,
        folders_truncated: false,
        selection: new Set<string>()
    })"""
)

content = content.replace(
    """        gallery.state.value = {
            state: 'ready',
            date_folder: '2023_02_02',
            entries: [entry('x.jpg'), entry('y.jpg')],
            truncated: false,
            selection: new Set<string>()
        }""",
    """        gallery.state.value = {
            state: 'ready',
            date_folder: '2023_02_02',
            sub_folder: '',
            folders: [],
            entries: [entry('x.jpg'), entry('y.jpg')],
            truncated: false,
            folders_truncated: false,
            selection: new Set<string>()
        }"""
)

content = content.replace(
    """        gallery.state.value = {
            state: 'ready',
            date_folder: '2023_02_02',
            entries: [entry('IMG_0001.jpg')],
            truncated: false,
            selection: new Set<string>()
        }""",
    """        gallery.state.value = {
            state: 'ready',
            date_folder: '2023_02_02',
            sub_folder: '',
            folders: [],
            entries: [entry('IMG_0001.jpg')],
            truncated: false,
            folders_truncated: false,
            selection: new Set<string>()
        }"""
)

with open("frontend/src/components/__tests__/PhotoGalleryModal.spec.ts", "w") as f:
    f.write(content)
