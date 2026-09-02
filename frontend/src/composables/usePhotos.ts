import { ref } from 'vue';
import { fetchAvailableDates, openPhotoFolder, type PhotoDirectoryStatus, type PhotoOpenOutcome } from '../api/photos';

export function usePhotos() {
    const directoryStatus = ref<PhotoDirectoryStatus | 'unknown'>('unknown');
    const folders = ref<string[]>([]);
    const lastFetchFailed = ref<boolean>(false);
    
    let requestSeq = 0;

    async function loadPhotoIndex(probe: string[]) {
        const seq = ++requestSeq;
        
        const result = await fetchAvailableDates(probe);
        
        if (seq !== requestSeq) {
            return;
        }

        if (result.kind === 'ok') {
            directoryStatus.value = result.status;
            folders.value = result.folders;
            lastFetchFailed.value = false;
        } else {
            // network error: only set lastFetchFailed. Leave directoryStatus and folders alone.
            lastFetchFailed.value = true;
        }
    }

    function resetPhotoState() {
        requestSeq++;
        directoryStatus.value = 'unknown';
        folders.value = [];
        lastFetchFailed.value = false;
    }

    async function openPhotos(date_folder: string): Promise<PhotoOpenOutcome> {
        const result = await openPhotoFolder(date_folder);
        
        if (result.kind === 'not_found') {
            folders.value = folders.value.filter(f => f !== date_folder);
        } else if (result.kind === 'unconfigured' || result.kind === 'unavailable') {
            directoryStatus.value = result.kind;
        }
        
        return result;
    }

    return {
        directoryStatus,
        folders,
        lastFetchFailed,
        loadPhotoIndex,
        resetPhotoState,
        openPhotos
    };
}
