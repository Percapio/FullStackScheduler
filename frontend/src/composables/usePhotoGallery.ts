import { ref, readonly } from 'vue';
import { fetchPhotoFiles, downloadPhotoArchive, type PhotoFileEntry } from '../api/photos';

export type GalleryState =
    | { state: 'closed' }
    | { state: 'loading'; date_folder: string }
    | { state: 'ready'; date_folder: string; entries: PhotoFileEntry[]; truncated: boolean; selection: Set<string> }
    | { state: 'error'; date_folder: string; message: string };

const state = ref<GalleryState>({ state: 'closed' });

export function usePhotoGallery() {
    const openGallery = async (date_folder: string) => {
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
        
        const outcome = await downloadPhotoArchive(date_folder, selection);
        if (outcome.kind === 'ok') {
            // Trigger download
            const url = URL.createObjectURL(outcome.blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = outcome.filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            
            clearSelection();
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
        closeGallery,
        toggleSelection,
        selectAll,
        clearSelection,
        downloadSelection
    };
}
