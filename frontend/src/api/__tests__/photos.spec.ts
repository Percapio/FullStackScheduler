import { describe, it, expect, vi } from 'vitest';
import { classify_photo_open_failure, photo_folder_for, fetchPhotoFiles, requestArchiveTicket, archiveDownloadUrl } from '../photos';
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

describe('classify_photo_open_failure', () => {
    it('returns not_found for 404 with kind', () => {
        const error = { response: { status: 404, data: { kind: 'not_found' } } };
        expect(classify_photo_open_failure('2023_07_24', error)).toEqual({ kind: 'not_found', date_folder: '2023_07_24' });
    });

    it('returns unconfigured for 409 unconfigured', () => {
        const error = { response: { status: 409, data: { kind: 'unconfigured' } } };
        expect(classify_photo_open_failure('2023_07_24', error)).toEqual({ kind: 'unconfigured' });
    });

    it('returns unavailable for 409 unavailable', () => {
        const error = { response: { status: 409, data: { kind: 'unavailable' } } };
        expect(classify_photo_open_failure('2023_07_24', error)).toEqual({ kind: 'unavailable' });
    });

    it('returns rate_limited for 429', () => {
        const error = { response: { status: 429, data: { kind: 'rate_limited', retry_after_seconds: 3 } } };
        expect(classify_photo_open_failure('2023_07_24', error)).toEqual({ kind: 'rate_limited', retry_after_seconds: 3 });
    });
    
    it('returns rate_limited with fallback', () => {
        const error = { response: { status: 429, data: { kind: 'rate_limited' } } };
        expect(classify_photo_open_failure('2023_07_24', error)).toEqual({ kind: 'rate_limited', retry_after_seconds: 2 });
    });

    it('returns shell_error for 500', () => {
        const error = { response: { status: 500, data: { kind: 'shell_error' } } };
        expect(classify_photo_open_failure('2023_07_24', error)).toEqual({ kind: 'shell_error' });
    });

    it('falls through to network for unrecognized 409 body', () => {
        const error = { response: { status: 409, data: { kind: 'some_other_thing' } } };
        expect(classify_photo_open_failure('2023_07_24', error)).toEqual({ kind: 'network', message: 'Unexpected error: 409' });
    });
    
    it('returns network for no response', () => {
        const error = { message: 'Timeout' };
        expect(classify_photo_open_failure('2023_07_24', error)).toEqual({ kind: 'network', message: 'Timeout' });
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
