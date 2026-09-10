import { apiClient } from './client'
import type { components } from './types.gen'

type PhotosDirRead = components['schemas']['PhotosDirRead']
type BrowseRead = components['schemas']['BrowseRead']
type PhotosDirWriteResponse = components['schemas']['PhotosDirWriteResponse']

export type PhotosDirSource = 'runtime' | 'env' | 'unset'

export type PhotosDirOutcome =
    | { kind: 'ok'; path: string | null; source: PhotosDirSource; configured: boolean; editable: boolean; folder_count?: number }
    | { kind: 'forbidden' }
    | { kind: 'invalid'; reason: 'blank' | 'not_absolute' | 'not_found' | 'not_a_dir' | 'not_readable' }
    | { kind: 'storage' }
    | { kind: 'network'; message: string }

export type BrowseOutcome =
    | { kind: 'ok'; parent: string | null; entries: Array<{ name: string; path: string }>; truncated: boolean }
    | { kind: 'forbidden' }
    | { kind: 'not_found' }
    | { kind: 'busy' }
    | { kind: 'network'; message: string }

export async function getPhotosDir(): Promise<PhotosDirOutcome> {
    try {
        const resp = await apiClient.get<PhotosDirRead>('/api/settings/photos-dir')
        return {
            kind: 'ok',
            path: resp.data.path,
            source: resp.data.source as PhotosDirSource,
            configured: resp.data.configured,
            editable: resp.data.editable
        }
    } catch (err: any) {
        if (err.response?.status === 403) {
            return { kind: 'forbidden' }
        }
        return { kind: 'network', message: err.message || String(err) }
    }
}

export async function browseDirectory(path: string, prefix: string = ''): Promise<BrowseOutcome> {
    try {
        const resp = await apiClient.get<BrowseRead>('/api/settings/browse', {
            params: { path, prefix },
            timeout: 60000 // extended timeout for slow SMB
        })
        return {
            kind: 'ok',
            parent: resp.data.parent,
            entries: resp.data.entries,
            truncated: resp.data.truncated
        }
    } catch (err: any) {
        if (err.response?.status === 403) {
            return { kind: 'forbidden' }
        }
        if (err.response?.status === 404) {
            return { kind: 'not_found' }
        }
        if (err.response?.status === 503) {
            return { kind: 'busy' }
        }
        return { kind: 'network', message: err.message || String(err) }
    }
}

export async function savePhotosDir(path: string): Promise<PhotosDirOutcome> {
    try {
        const resp = await apiClient.put<PhotosDirWriteResponse>('/api/settings/photos-dir', { path })
        return {
            kind: 'ok',
            path: resp.data.path,
            source: resp.data.source as PhotosDirSource,
            configured: resp.data.configured,
            editable: resp.data.editable,
            folder_count: resp.data.folder_count
        }
    } catch (err: any) {
        if (err.response?.status === 403) {
            return { kind: 'forbidden' }
        }
        if (err.response?.status === 422) {
            const detail = err.response.data?.detail
            if (detail && detail.kind) {
                return { kind: 'invalid', reason: detail.kind }
            }
        }
        if (err.response?.status === 500) {
            const detail = err.response.data?.detail
            if (detail && detail.kind === 'storage') {
                return { kind: 'storage' }
            }
        }
        return { kind: 'network', message: err.message || String(err) }
    }
}

export interface AutoCopyRead {
    enabled: boolean
    source: string | null
    source_configured: boolean
    scheduled_time: string | null
    editable: boolean
    running: boolean
    run_started_at: string | null
    last_run_finished_at: string | null
    last_run_outcome: string | null
    last_run_files_copied: number
    last_run_files_failed: number
    last_run_dates_skipped: string[]
    last_completed_date: string | null
    last_error_kind: string
}

export type AutoCopyOutcome =
    | { kind: 'ok'; data: AutoCopyRead }
    | { kind: 'forbidden' }
    | { kind: 'invalid'; reason: string }
    | { kind: 'storage' }
    | { kind: 'network'; message: string }
    | { kind: 'conflict'; reason: string }

export async function getAutoCopy(): Promise<AutoCopyOutcome> {
    try {
        const resp = await apiClient.get<AutoCopyRead>('/api/settings/auto-copy')
        return { kind: 'ok', data: resp.data }
    } catch (err: any) {
        if (err.response?.status === 403) {
            return { kind: 'forbidden' }
        }
        return { kind: 'network', message: err.message || String(err) }
    }
}

export async function saveAutoCopy(enabled: boolean, source: string, scheduled_time: string): Promise<AutoCopyOutcome> {
    try {
        const resp = await apiClient.put<AutoCopyRead>('/api/settings/auto-copy', { enabled, source, scheduled_time })
        return { kind: 'ok', data: resp.data }
    } catch (err: any) {
        if (err.response?.status === 403) {
            return { kind: 'forbidden' }
        }
        if (err.response?.status === 422) {
            const detail = err.response.data?.detail
            if (detail && detail.kind) {
                return { kind: 'invalid', reason: detail.kind }
            }
        }
        if (err.response?.status === 500) {
            return { kind: 'storage' }
        }
        return { kind: 'network', message: err.message || String(err) }
    }
}

export async function runAutoCopyNow(): Promise<AutoCopyOutcome> {
    try {
        await apiClient.post('/api/settings/auto-copy/run')
        return getAutoCopy()
    } catch (err: any) {
        if (err.response?.status === 403) {
            return { kind: 'forbidden' }
        }
        if (err.response?.status === 409) {
            const detail = err.response.data?.detail
            return { kind: 'conflict', reason: detail?.kind || 'conflict' }
        }
        return { kind: 'network', message: err.message || String(err) }
    }
}
