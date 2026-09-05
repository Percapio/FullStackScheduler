import re

with open("frontend/src/composables/usePhotoGallery.ts", "r") as f:
    content = f.read()

content = content.replace(
    """export type GalleryState =
    | { state: 'closed' }
    | { state: 'loading'; date_folder: string }
    | { state: 'ready'; date_folder: string; entries: PhotoFileEntry[]; truncated: boolean; selection: Set<string> }
    | { state: 'error'; date_folder: string; message: string };""",
    """export type GalleryState =
    | { state: 'closed' }
    | { state: 'loading'; date_folder: string; sub_folder: string; seq: number }
    | { state: 'ready'; date_folder: string; sub_folder: string; folders: string[]; entries: PhotoFileEntry[]; truncated: boolean; folders_truncated: boolean; selection: Set<string> }
    | { state: 'error'; date_folder: string; sub_folder: string; message: string };"""
)

content = content.replace(
    """const state = ref<GalleryState>({ state: 'closed' });

export function usePhotoGallery() {""",
    """const state = ref<GalleryState>({ state: 'closed' });
let requestSeq: number = 0;

export function usePhotoGallery() {"""
)

content = content.replace(
    """    const openGallery = async (date_folder: string) => {
        state.value = { state: 'loading', date_folder };
        const outcome = await fetchPhotoFiles(date_folder);
        
        if (state.value.state !== 'loading' || state.value.date_folder !== date_folder) {
            return; // superseded
        }
        
        if (outcome.kind === 'ok') {
            if (outcome.status === 'ok') {
                state.value = {
                    state: 'ready',
                    date_folder,
                    entries: outcome.entries,
                    truncated: outcome.truncated,
                    selection: new Set()
                };
            } else {
                state.value = {
                    state: 'error',
                    date_folder,
                    message: `Directory status: ${outcome.status}`
                };
            }
        } else {
            state.value = {
                state: 'error',
                date_folder,
                message: outcome.message
            };
        }
    };""",
    """    const loadFolder = async (date_folder: string, sub_folder: string) => {
        requestSeq += 1;
        const currentSeq = requestSeq;
        state.value = { state: 'loading', date_folder, sub_folder, seq: currentSeq };
        const outcome = await fetchPhotoFiles(date_folder, sub_folder);
        
        if (state.value.state !== 'loading' || state.value.seq !== currentSeq) {
            return; // superseded
        }
        
        if (outcome.kind === 'ok') {
            if (outcome.status === 'ok') {
                state.value = {
                    state: 'ready',
                    date_folder,
                    sub_folder,
                    folders: outcome.folders,
                    entries: outcome.entries,
                    truncated: outcome.truncated,
                    folders_truncated: outcome.folders_truncated,
                    selection: new Set()
                };
            } else {
                state.value = {
                    state: 'error',
                    date_folder,
                    sub_folder,
                    message: `Directory status: ${outcome.status}`
                };
            }
        } else {
            state.value = {
                state: 'error',
                date_folder,
                sub_folder,
                message: outcome.message
            };
        }
    };

    const openGallery = (date_folder: string) => loadFolder(date_folder, "");
    
    const navigateTo = (folder_name: string) => {
        if (state.value.state !== 'ready' || state.value.sub_folder !== "") return;
        loadFolder(state.value.date_folder, folder_name);
    };

    const navigateUp = () => {
        if (state.value.state !== 'ready') return;
        loadFolder(state.value.date_folder, "");
    };"""
)

content = content.replace(
    """        const date_folder = state.value.date_folder;
        
        const outcome = await requestArchiveTicket(date_folder, selection);""",
    """        const date_folder = state.value.date_folder;
        const sub_folder = state.value.sub_folder;
        
        const outcome = await requestArchiveTicket(date_folder, sub_folder, selection);"""
)

content = content.replace(
    """        openGallery,
        closeGallery,""",
    """        openGallery,
        navigateTo,
        navigateUp,
        closeGallery,"""
)

with open("frontend/src/composables/usePhotoGallery.ts", "w") as f:
    f.write(content)
