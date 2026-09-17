import { ref, readonly } from 'vue';
import {
    fetchPhotoFiles, requestArchiveTicket, archiveDownloadUrl, fetchArchiveStatus,
    type PhotoFileEntry, type ArchiveStatusOutcome, type ArchiveTicketOutcome
} from '../api/photos';
import { isServerFailureReason, type DownloadProgress, type FailureReason } from './archiveProgress';

export type { DownloadProgress } from './archiveProgress';

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

// Phase 31 §6.3: 1 s for the first 15 s after hand-off, then 10 s, capped at the
// server's session budget. The switch is on elapsed time, not on observed state.
export const FAST_POLL_WINDOW_SECONDS = 15;
export const FAST_POLL_MS = 1000;
export const SLOW_POLL_MS = 10000;
export const POLL_BUDGET_SECONDS = 1800;

const state = ref<GalleryState>({ state: 'closed' });
let requestSeq: number = 0;

const downloadProgress = ref<DownloadProgress>({ kind: 'Idle' });
let downloadPollSeq = 0;
let activeDownloadPoll: AbortController | null = null;

function mintFailureReason(outcome: Exclude<ArchiveTicketOutcome, { kind: 'ok' }>): FailureReason {
    switch (outcome.kind) {
        case 'lan_cap_exceeded': return 'LanCapExceeded';
        case 'busy': return 'PermitsExhausted';
        case 'not_found': return 'FolderNotFound';
        case 'network': return 'NetworkError';
    }
}

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

    // Mints, hands off, then polls the status endpoint until a terminal state.
    // Exactly one terminal transition per invocation; a second invocation
    // aborts the first poll's in-flight request and silences it (Phase 31 §6.4).
    const downloadSelection = async (): Promise<void> => {
        if (state.value.state !== 'ready') return;

        downloadPollSeq += 1;
        const currentSeq = downloadPollSeq;
        activeDownloadPoll?.abort();
        const abortController = new AbortController();
        activeDownloadPoll = abortController;

        const selection = Array.from(state.value.selection);
        const date_folder = state.value.date_folder;
        const sub_folder = state.value.sub_folder;

        downloadProgress.value = { kind: 'Minting' };
        const outcome = await requestArchiveTicket(date_folder, sub_folder, selection);

        if (downloadPollSeq !== currentSeq) return;

        if (outcome.kind !== 'ok') {
            downloadProgress.value = { kind: 'Failed', reason: mintFailureReason(outcome), filename: null };
            return;
        }

        const filename = outcome.filename;
        const token = outcome.token;
        handOffToBrowser(archiveDownloadUrl(token));
        downloadProgress.value = { kind: 'Preparing', filename };

        const startTime = performance.now();

        while (true) {
            if (downloadPollSeq !== currentSeq) return;

            const elapsedSeconds = (performance.now() - startTime) / 1000;
            if (elapsedSeconds > POLL_BUDGET_SECONDS) {
                downloadProgress.value = { kind: 'Failed', reason: 'PollAbandoned', lost: 'GaveUp', filename };
                return;
            }

            const cadenceMs = elapsedSeconds < FAST_POLL_WINDOW_SECONDS ? FAST_POLL_MS : SLOW_POLL_MS;
            await new Promise(resolve => setTimeout(resolve, cadenceMs));

            if (downloadPollSeq !== currentSeq) return;

            let status: ArchiveStatusOutcome;
            try {
                status = await fetchArchiveStatus(token, abortController.signal);
            } catch {
                return;
            }

            if (downloadPollSeq !== currentSeq) return;

            if (status.state === 'Terminal') {
                if (status.outcome === 'Completed') {
                    downloadProgress.value = {
                        kind: 'Succeeded',
                        filename,
                        bytes: status.bytes_sent,
                        entries: status.entry_count,
                        missing: status.unresolved_count
                    };
                } else if (isServerFailureReason(status.outcome)) {
                    downloadProgress.value = { kind: 'Failed', reason: status.outcome, filename };
                } else {
                    downloadProgress.value = { kind: 'Failed', reason: 'PollAbandoned', lost: 'Unknown', filename };
                }
                return;
            }

            const polledAtSeconds = (performance.now() - startTime) / 1000;
            if (status.state === 'Streaming') {
                downloadProgress.value = { kind: 'Downloading', filename };
            } else if (status.state === 'Unknown' && polledAtSeconds >= FAST_POLL_WINDOW_SECONDS) {
                downloadProgress.value = { kind: 'Failed', reason: 'PollAbandoned', lost: 'Unknown', filename };
                return;
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
