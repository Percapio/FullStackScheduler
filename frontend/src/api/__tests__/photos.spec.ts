import { describe, it, expect, vi } from 'vitest';
import { photo_folder_for, fetchPhotoFiles, requestArchiveTicket, archiveDownloadUrl } from '../photos';
import { apiClient } from '../client';

vi.mock('../client', () => ({
    apiClient: {
        get: vi.fn(),
        post: vi.fn()
    },
    baseURL: 'mock-base'
}));

describe('photos api', () => {
    it('fetchPhotoFiles returns data on ok', async () => {
        vi.mocked(apiClient.get).mockResolvedValueOnce({
            data: { status: 'ok', entries: [], truncated: false }
        });
        
        const res = await fetchPhotoFiles('2023_01_01');
        expect(res).toEqual({ kind: 'ok', status: 'ok', entries: [], truncated: false });
    });
    
    it('requestArchiveTicket handles busy', async () => {
        const error = {
            response: { status: 503, data: { kind: 'busy' } }
        };
        vi.mocked(apiClient.post).mockRejectedValueOnce(error);
        
        const res = await requestArchiveTicket('2023_01_01', '', []);
        expect(res).toEqual({ kind: 'busy' });
    });

    it('requestArchiveTicket maps 403 to lan_cap_exceeded', async () => {
        const error = {
            response: { status: 403, data: { kind: 'lan_cap_exceeded', limit: 'files' } }
        };
        vi.mocked(apiClient.post).mockRejectedValueOnce(error);
        
        const res = await requestArchiveTicket('2023_01_01', '', []);
        expect(res).toEqual({ kind: 'lan_cap_exceeded', limit: 'files' });
    });

    it('archiveDownloadUrl percent-encodes', () => {
        expect(archiveDownloadUrl('a+b/c')).toContain('a%2Bb%2Fc');
    });
});

describe('photo_folder_for', () => {
    it('translates valid date', () => {
        expect(photo_folder_for({ shipped_at: '2023-07-24' } as any)).toBe('2023_07_24');
    });

    it('returns null for null', () => {
        expect(photo_folder_for({ shipped_at: null } as any)).toBeNull();
    });

    it('returns null for datetime (regression prevention)', () => {
        expect(photo_folder_for({ shipped_at: '2023-07-24T00:00:00' } as any)).toBeNull();
    });

    it('returns null for other shapes', () => {
        expect(photo_folder_for({ shipped_at: '' } as any)).toBeNull();
        expect(photo_folder_for({ shipped_at: '07/24/2023' } as any)).toBeNull();
        expect(photo_folder_for({ shipped_at: '2023-7-4' } as any)).toBeNull();
    });
});
