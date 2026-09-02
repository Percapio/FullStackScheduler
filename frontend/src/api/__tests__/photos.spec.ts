import { describe, expect, it } from 'vitest';
import { classify_photo_open_failure, photo_folder_for } from '../photos';

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
