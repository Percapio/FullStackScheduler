import { ref } from 'vue';
import { fetchAvailableDates, type PhotoDirectoryStatus } from '../api/photos';

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

    return {
        directoryStatus,
        folders,
        lastFetchFailed,
        loadPhotoIndex,
        resetPhotoState
    };
}
