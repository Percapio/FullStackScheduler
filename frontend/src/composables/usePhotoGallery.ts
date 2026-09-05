import { ref, readonly } from 'vue';
import { fetchPhotoFiles, requestArchiveTicket, archiveDownloadUrl, type PhotoFileEntry } from '../api/photos';

export type GalleryState =
    | { state: 'closed' }
    | { state: 'loading'; date_folder: string; sub_folder: string; seq: number }
    | { state: 'ready'; date_folder: string; sub_folder: string; folders: string[]; entries: PhotoFileEntry[]; truncated: boolean; folders_truncated: boolean; selection: Set<string> }
    | { state: 'error'; date_folder: string; sub_folder: string; message: string };

const DOWNLOAD_FRAME_ID = 'archive-download-frame';

// Handoff target. A hidden iframe rather than window.location.assign: if the
// GET returns JSON instead of an attachment (expired token, lost semaphore
// race) a top-level navigation would replace the SPA with a raw JSON page and
// destroy all app state. Inside the frame, the same response is invisible and
// discarded. Attachment responses never render, so the success path is
// identical either way.
function handOffToBrowser(url: string) {
    let frame = document.getElementById(DOWNLOAD_FRAME_ID) as HTMLIFrameElement | null;
    if (!frame) {
        frame = document.createElement('iframe');
        frame.id = DOWNLOAD_FRAME_ID;
        frame.setAttribute('aria-hidden', 'true');
        frame.style.display = 'none';
        document.body.appendChild(frame);
    }
    frame.src = url;
}

const state = ref<GalleryState>({ state: 'closed' });
let requestSeq: number = 0;

export function usePhotoGallery() {
    const loadFolder = async (date_folder: string, sub_folder: string) => {
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
    };
    
    const closeGallery = () => {
        state.value = { state: 'closed' };
    };
    
    const toggleSelection = (filename: string) => {
        if (state.value.state !== 'ready') return;
        const s = new Set(state.value.selection);
        if (s.has(filename)) {
            s.delete(filename);
        } else {
            s.add(filename);
        }
        state.value = { ...state.value, selection: s };
    };
    
    const selectAll = () => {
        if (state.value.state !== 'ready') return;
        state.value = {
            ...state.value,
            selection: new Set(state.value.entries.map(e => e.name))
        };
    };
    
    const clearSelection = () => {
        if (state.value.state !== 'ready') return;
        state.value = { ...state.value, selection: new Set() };
    };
    
    const downloadSelection = async (): Promise<string | null> => {
        if (state.value.state !== 'ready') return 'Gallery not ready';
        
        const selection = Array.from(state.value.selection);
        const date_folder = state.value.date_folder;
        const sub_folder = state.value.sub_folder;
        
        const outcome = await requestArchiveTicket(date_folder, sub_folder, selection);
        if (outcome.kind === 'ok') {
            handOffToBrowser(archiveDownloadUrl(outcome.token));
            return null;
        } else if (outcome.kind === 'not_found') {
            return `Not found: ${outcome.status}`;
        } else if (outcome.kind === 'lan_cap_exceeded') {
            return `LAN bulk download limit exceeded (${outcome.limit})`;
        } else if (outcome.kind === 'busy') {
            return 'Server is busy processing other archives. Try again in a few seconds.';
        } else {
            return outcome.message;
        }
    };
    
    return {
        state: readonly(state),
        openGallery,
        navigateTo,
        navigateUp,
        closeGallery,
        toggleSelection,
        selectAll,
        clearSelection,
        downloadSelection
    };
}
