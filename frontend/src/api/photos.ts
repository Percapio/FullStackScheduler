import type { components } from './types.gen';
import { apiClient } from './client';

export type PhotoDirectoryStatus = 'unknown' | 'unconfigured' | 'unavailable' | 'ok';

export type PhotoIndexOutcome =
    | { kind: 'ok'; status: PhotoDirectoryStatus; folders: string[]; truncated: boolean }
    | { kind: 'network'; message: string };

export type PhotoOpenOutcome =
    | { kind: 'ok'; date_folder: string }
    | { kind: 'not_found'; date_folder: string }
    | { kind: 'rate_limited'; retry_after_seconds: number }
    | { kind: 'unconfigured' }
    | { kind: 'unavailable' }
    | { kind: 'shell_error' }
    | { kind: 'network'; message: string };

export function classify_photo_open_failure(
    date_folder: string,
    error: any
): PhotoOpenOutcome {
    if (!error || !error.response) {
        return { kind: 'network', message: error?.message || 'Network error' };
    }

    const status = error.response.status;
    const body = error.response.data;

    if (status === 404 && body?.kind === 'not_found') {
        return { kind: 'not_found', date_folder };
    }
    if (status === 409 && body?.kind === 'unconfigured') {
        return { kind: 'unconfigured' };
    }
    if (status === 409 && body?.kind === 'unavailable') {
        return { kind: 'unavailable' };
    }
    if (status === 429 && body?.kind === 'rate_limited') {
        return { kind: 'rate_limited', retry_after_seconds: body.retry_after_seconds || 2 };
    }
    if (status === 500 && body?.kind === 'shell_error') {
        return { kind: 'shell_error' };
    }

    return { kind: 'network', message: `Unexpected error: ${status}` };
}

export function photo_folder_for(job: components['schemas']['JobReadExpanded']): string | null {
    if (!job || !job.shipped_at) return null;
    const s = job.shipped_at;
    if (typeof s !== 'string') return null;
    if (!/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(s)) return null;
    return s.replace(/-/g, '_');
}

export async function fetchAvailableDates(probe: string[]): Promise<PhotoIndexOutcome> {
    try {
        const searchParams = new URLSearchParams();
        for (const p of probe) {
            searchParams.append('probe', p);
        }
        const qs = searchParams.toString();
        const url = `/api/photos/available-dates${qs ? '?' + qs : ''}`;
        
        // Timeout override for cold cache against dead share (50s)
        const res = await apiClient.get(url, { timeout: 50_000 });
        return {
            kind: 'ok',
            status: res.data.status,
            folders: res.data.folders,
            truncated: res.data.truncated
        };
    } catch (e: any) {
        return { kind: 'network', message: e.message || 'Network error' };
    }
}

export async function openPhotoFolder(date_folder: string): Promise<PhotoOpenOutcome> {
    try {
        await apiClient.post('/api/photos/open', { date_folder });
        return { kind: 'ok', date_folder };
    } catch (e: any) {
        return classify_photo_open_failure(date_folder, e);
    }
}
