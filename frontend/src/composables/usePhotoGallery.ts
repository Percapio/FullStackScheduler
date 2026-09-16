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
    
    type FailureReason = string; // Will hold terminal reasons or 'PollAbandoned'
    
    type DownloadProgress =
        | { kind: 'Idle' }
        | { kind: 'Minting' }
        | { kind: 'HandedOff', filename: string }
        | { kind: 'Failed', reason: FailureReason, filename: string | null }
        | { kind: 'Succeeded', filename: string, bytes: number, entries: number, unresolved: number };

    const downloadProgress = ref<DownloadProgress>({ kind: 'Idle' });
    let downloadPollSeq = 0;

    const downloadSelection = async (): Promise<void> => {
        if (state.value.state !== 'ready') return;
        
        downloadPollSeq += 1;
        const currentSeq = downloadPollSeq;
        let abortController = new AbortController();
        
        // Expose abort mechanism if needed, but supersession handles it
        // We actually want to store abortController so we can abort previous poll
        // Let's use a module-level variable for it
        if ((window as any)._activeDownloadPoll) {
            (window as any)._activeDownloadPoll.abort();
        }
        (window as any)._activeDownloadPoll = abortController;

        const selection = Array.from(state.value.selection);
        const date_folder = state.value.date_folder;
        const sub_folder = state.value.sub_folder;
        
        downloadProgress.value = { kind: 'Minting' };
        const outcome = await requestArchiveTicket(date_folder, sub_folder, selection);
        
        if (downloadPollSeq !== currentSeq) return;

        if (outcome.kind !== 'ok') {
            let reason = 'FailedStart';
            if (outcome.kind === 'lan_cap_exceeded' || outcome.kind === 'busy') {
                reason = 'PermitsExhausted';
            } else if (outcome.kind === 'not_found') {
                reason = 'FolderNotFound';
            }
            downloadProgress.value = { kind: 'Failed', reason, filename: null };
            return;
        }

        const filename = outcome.filename;
        const token = outcome.token;
        handOffToBrowser(archiveDownloadUrl(token));
        downloadProgress.value = { kind: 'HandedOff', filename };

        const startTime = performance.now();
        const budgetSeconds = 1800; // Same as backend budget
        
        while (true) {
            if (downloadPollSeq !== currentSeq) return;
            
            const elapsedSeconds = (performance.now() - startTime) / 1000;
            if (elapsedSeconds > budgetSeconds) {
                downloadProgress.value = { kind: 'Failed', reason: 'PollAbandoned', filename };
                return;
            }
            
            const cadenceMs = elapsedSeconds <= 15 ? 1000 : 10000;
            await new Promise(r => setTimeout(r, cadenceMs));
            
            if (downloadPollSeq !== currentSeq) return;
            
            try {
                const { fetchArchiveStatus } = await import('../api/photos');
                const status = await fetchArchiveStatus(token, abortController.signal);
                
                if (downloadPollSeq !== currentSeq) return;
                
                if (status.state === 'Terminal') {
                    if (status.outcome === 'Completed') {
                        downloadProgress.value = {
                            kind: 'Succeeded',
                            filename,
                            bytes: status.bytes_sent,
                            entries: status.entry_count,
                            unresolved: status.unresolved_count
                        };
                    } else {
                        downloadProgress.value = { kind: 'Failed', reason: status.outcome, filename };
                    }
                    return;
                } else if (status.state === 'Unknown' && elapsedSeconds > 15) {
                    downloadProgress.value = { kind: 'Failed', reason: 'PollAbandoned', filename };
                    return;
                }
                // Pending, Streaming, or Unknown within fast window: keep polling
            } catch (e: any) {
                if (e.name === 'CanceledError' || e.code === 'ERR_CANCELED') {
                    return; // Superseded
                }
                // Log and keep polling on network error? Or abort?
                // Let's keep polling, network might recover
            }
        }
    };
    
    return {
        state: readonly(state),
        downloadProgress: readonly(downloadProgress),
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
