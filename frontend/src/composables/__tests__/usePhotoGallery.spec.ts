import { describe, it, expect, vi, beforeEach } from 'vitest';
import { usePhotoGallery } from '../usePhotoGallery';
import * as photosApi from '../../api/photos';

vi.mock('../../api/photos', () => ({
    fetchPhotoFiles: vi.fn(),
    downloadPhotoArchive: vi.fn()
}));

describe('usePhotoGallery', () => {
    let gallery: ReturnType<typeof usePhotoGallery>;
    
    beforeEach(() => {
        gallery = usePhotoGallery();
        gallery.closeGallery(); // reset state
    });

    it('opens gallery and loads entries', async () => {
        vi.mocked(photosApi.fetchPhotoFiles).mockResolvedValueOnce({
            kind: 'ok', status: 'ok', entries: [
                { name: '1.jpg', size_bytes: 10, mtime_ns: 0, version: '1', previewable: true }
            ], truncated: false
        });
        
        await gallery.openGallery('2023_01_01');
        
        expect(gallery.state.value.state).toBe('ready');
        if (gallery.state.value.state === 'ready') {
            expect(gallery.state.value.entries.length).toBe(1);
        }
    });
    
    it('handles toggle and clear selection', async () => {
        vi.mocked(photosApi.fetchPhotoFiles).mockResolvedValueOnce({
            kind: 'ok', status: 'ok', entries: [
                { name: '1.jpg', size_bytes: 10, mtime_ns: 0, version: '1', previewable: true }
            ], truncated: false
        });
        
        await gallery.openGallery('2023_01_01');
        gallery.toggleSelection('1.jpg');
        
        if (gallery.state.value.state === 'ready') {
            expect(gallery.state.value.selection.has('1.jpg')).toBe(true);
        }
        
        gallery.clearSelection();
        if (gallery.state.value.state === 'ready') {
            expect(gallery.state.value.selection.size).toBe(0);
        }
    });
});
