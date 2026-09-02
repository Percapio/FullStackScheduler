import { describe, expect, it, vi } from 'vitest';
import { usePhotos } from '../usePhotos';
import * as photosApi from '../../api/photos';

vi.mock('../../api/photos', () => ({
    fetchAvailableDates: vi.fn(),
    openPhotoFolder: vi.fn()
}));

describe('usePhotos', () => {
    it('out-of-order resolution preserves newest request', async () => {
        const fetchSpy = vi.mocked(photosApi.fetchAvailableDates);
        
        // Request A resolves late
        let resolveA: (value: import('../../api/photos').PhotoIndexOutcome) => void;
        const promiseA = new Promise<import('../../api/photos').PhotoIndexOutcome>(r => { resolveA = r; });
        
        // Request B resolves early
        let resolveB: (value: import('../../api/photos').PhotoIndexOutcome) => void;
        const promiseB = new Promise<import('../../api/photos').PhotoIndexOutcome>(r => { resolveB = r; });

        fetchSpy.mockReturnValueOnce(promiseA).mockReturnValueOnce(promiseB);

        const { loadPhotoIndex, folders } = usePhotos();
        
        loadPhotoIndex(['A']);
        loadPhotoIndex(['B']);
        
        // B resolves
        resolveB!({ kind: 'ok', status: 'ok', folders: ['B'], truncated: false });
        await Promise.resolve(); // flush
        
        expect(folders.value).toEqual(['B']);
        
        // A resolves
        resolveA!({ kind: 'ok', status: 'ok', folders: ['A'], truncated: false });
        await Promise.resolve(); // flush
        
        // Should still be B
        expect(folders.value).toEqual(['B']);
    });

    it('network outcome writes only last_fetch_failed', async () => {
        const fetchSpy = vi.mocked(photosApi.fetchAvailableDates);
        fetchSpy.mockResolvedValueOnce({ kind: 'ok', status: 'ok', folders: ['A'], truncated: false });
        
        const { loadPhotoIndex, directoryStatus, folders, lastFetchFailed } = usePhotos();
        
        await loadPhotoIndex(['A']);
        expect(directoryStatus.value).toBe('ok');
        expect(folders.value).toEqual(['A']);
        expect(lastFetchFailed.value).toBe(false);
        
        fetchSpy.mockResolvedValueOnce({ kind: 'network', message: 'err' });
        await loadPhotoIndex(['A']);
        
        // lastFetchFailed is true, but others untouched
        expect(lastFetchFailed.value).toBe(true);
        expect(directoryStatus.value).toBe('ok');
        expect(folders.value).toEqual(['A']);
    });

    it('network outcome from unknown stays unknown', async () => {
        const fetchSpy = vi.mocked(photosApi.fetchAvailableDates);
        fetchSpy.mockResolvedValueOnce({ kind: 'network', message: 'err' });
        
        const { loadPhotoIndex, directoryStatus, lastFetchFailed } = usePhotos();
        
        await loadPhotoIndex(['A']);
        
        expect(lastFetchFailed.value).toBe(true);
        expect(directoryStatus.value).toBe('unknown');
    });

    it('open_photos not_found removes exactly that name', async () => {
        const fetchSpy = vi.mocked(photosApi.fetchAvailableDates);
        fetchSpy.mockResolvedValueOnce({ kind: 'ok', status: 'ok', folders: ['A', 'B'], truncated: false });
        
        const { loadPhotoIndex, openPhotos, folders } = usePhotos();
        await loadPhotoIndex(['A', 'B']);
        
        const openSpy = vi.mocked(photosApi.openPhotoFolder);
        openSpy.mockResolvedValueOnce({ kind: 'not_found', date_folder: 'A' });
        
        await openPhotos('A');
        
        expect(folders.value).toEqual(['B']);
    });

    it('open_photos unconfigured updates directoryStatus', async () => {
        const fetchSpy = vi.mocked(photosApi.fetchAvailableDates);
        fetchSpy.mockResolvedValueOnce({ kind: 'ok', status: 'ok', folders: ['A'], truncated: false });
        
        const { loadPhotoIndex, openPhotos, directoryStatus } = usePhotos();
        await loadPhotoIndex(['A']);
        
        const openSpy = vi.mocked(photosApi.openPhotoFolder);
        openSpy.mockResolvedValueOnce({ kind: 'unconfigured' });
        
        await openPhotos('A');
        
        expect(directoryStatus.value).toBe('unconfigured');
    });

    it('resetPhotoState returns to initial values', async () => {
        const fetchSpy = vi.mocked(photosApi.fetchAvailableDates);
        fetchSpy.mockResolvedValueOnce({ kind: 'ok', status: 'ok', folders: ['A'], truncated: false });
        
        const { loadPhotoIndex, resetPhotoState, directoryStatus, folders, lastFetchFailed } = usePhotos();
        await loadPhotoIndex(['A']);
        
        resetPhotoState();
        
        expect(directoryStatus.value).toBe('unknown');
        expect(folders.value).toEqual([]);
        expect(lastFetchFailed.value).toBe(false);
    });
});
