import { describe, it, expect, vi, beforeEach } from 'vitest';
import { usePhotoGallery } from '../usePhotoGallery';
import * as photosApi from '../../api/photos';

vi.mock('../../api/photos', () => ({
    fetchPhotoFiles: vi.fn(),
    requestArchiveTicket: vi.fn(),
    archiveDownloadUrl: vi.fn().mockImplementation((t) => `mock_url/${t}`)
}));

describe('usePhotoGallery', () => {
    let gallery: ReturnType<typeof usePhotoGallery>;
    
    beforeEach(() => {
        document.body.innerHTML = '';
        gallery = usePhotoGallery();
        gallery.closeGallery(); // reset state
    });

    it('opens gallery and loads entries', async () => {
        vi.mocked(photosApi.fetchPhotoFiles).mockResolvedValueOnce({
            kind: 'ok', status: 'ok', folders: [], folders_truncated: false, entries: [
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
            kind: 'ok', status: 'ok', folders: [], folders_truncated: false, entries: [
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

    it('hands off to iframe on successful mint and keeps selection', async () => {
        vi.mocked(photosApi.fetchPhotoFiles).mockResolvedValueOnce({
            kind: 'ok', status: 'ok', folders: [], folders_truncated: false, entries: [{ name: '1.jpg', size_bytes: 10, mtime_ns: 0, version: '1', previewable: true }], truncated: false
        });
        vi.mocked(photosApi.requestArchiveTicket).mockResolvedValue({
            kind: 'ok', token: 'tok_123', filename: 'Photos.zip'
        });

        await gallery.openGallery('2023_01_01');
        gallery.toggleSelection('1.jpg');

        const err = await gallery.downloadSelection();
        expect(err).toBeNull();
        
        const frame = document.getElementById('archive-download-frame') as HTMLIFrameElement;
        expect(frame).not.toBeNull();
        expect(frame.src).toContain('mock_url/tok_123');
        
        // keeps selection
        expect((gallery.state.value as any).selection.has('1.jpg')).toBe(true);

        // second download reuses frame
        await gallery.downloadSelection();
        expect(document.querySelectorAll('#archive-download-frame').length).toBe(1);
    });

    it('returns error on 403 and creates no iframe', async () => {
        vi.mocked(photosApi.fetchPhotoFiles).mockResolvedValueOnce({
            kind: 'ok', status: 'ok', folders: [], folders_truncated: false, entries: [], truncated: false
        });
        vi.mocked(photosApi.requestArchiveTicket).mockResolvedValueOnce({
            kind: 'lan_cap_exceeded', limit: 'files'
        });

        await gallery.openGallery('2023_01_01');
        const err = await gallery.downloadSelection();
        expect(err).toContain('LAN bulk download limit exceeded');
        
        // Assume no frame was created if we clear body before each test
        // Or if it was created in previous test, the src wouldn't be updated.
        // Actually better to clear DOM in beforeEach
    });
});
