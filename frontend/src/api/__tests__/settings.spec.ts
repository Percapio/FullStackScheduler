import { describe, it, expect, vi, beforeEach } from 'vitest'
import { getPhotosDir, browseDirectory, savePhotosDir } from '../settings'
import { apiClient } from '../client'

vi.mock('../client', () => ({
    apiClient: {
        get: vi.fn(),
        put: vi.fn(),
    }
}))

describe('settings API', () => {
    beforeEach(() => {
        vi.clearAllMocks()
    })

    describe('getPhotosDir', () => {
        it('returns ok on success', async () => {
            vi.mocked(apiClient.get).mockResolvedValueOnce({
                data: { path: 'C:\\test', source: 'env', configured: true, editable: true }
            })
            const res = await getPhotosDir()
            expect(res).toEqual({
                kind: 'ok',
                path: 'C:\\test',
                source: 'env',
                configured: true,
                editable: true
            })
        })

        it('returns forbidden on 403', async () => {
            vi.mocked(apiClient.get).mockRejectedValueOnce({ response: { status: 403 } })
            const res = await getPhotosDir()
            expect(res).toEqual({ kind: 'forbidden' })
        })

        it('returns network error on other failures', async () => {
            vi.mocked(apiClient.get).mockRejectedValueOnce(new Error('Network offline'))
            const res = await getPhotosDir()
            expect(res).toEqual({ kind: 'network', message: 'Network offline' })
        })
    })

    describe('browseDirectory', () => {
        it('returns ok on success', async () => {
            vi.mocked(apiClient.get).mockResolvedValueOnce({
                data: { parent: 'C:\\', entries: [{ name: 'test', path: 'C:\\test' }], truncated: false }
            })
            const res = await browseDirectory('C:\\', 't')
            expect(res).toEqual({
                kind: 'ok',
                parent: 'C:\\',
                entries: [{ name: 'test', path: 'C:\\test' }],
                truncated: false
            })
        })

        it('returns forbidden on 403', async () => {
            vi.mocked(apiClient.get).mockRejectedValueOnce({ response: { status: 403 } })
            const res = await browseDirectory('C:\\')
            expect(res).toEqual({ kind: 'forbidden' })
        })

        it('returns not_found on 404', async () => {
            vi.mocked(apiClient.get).mockRejectedValueOnce({ response: { status: 404 } })
            const res = await browseDirectory('C:\\')
            expect(res).toEqual({ kind: 'not_found' })
        })

        it('returns busy on 503', async () => {
            vi.mocked(apiClient.get).mockRejectedValueOnce({ response: { status: 503 } })
            const res = await browseDirectory('C:\\')
            expect(res).toEqual({ kind: 'busy' })
        })
    })

    describe('savePhotosDir', () => {
        it('returns ok on success', async () => {
            vi.mocked(apiClient.put).mockResolvedValueOnce({
                data: { path: 'C:\\new', source: 'runtime', configured: true, editable: true, folder_count: 5 }
            })
            const res = await savePhotosDir('C:\\new')
            expect(res).toEqual({
                kind: 'ok',
                path: 'C:\\new',
                source: 'runtime',
                configured: true,
                editable: true,
                folder_count: 5
            })
        })

        it('returns forbidden on 403', async () => {
            vi.mocked(apiClient.put).mockRejectedValueOnce({ response: { status: 403 } })
            const res = await savePhotosDir('C:\\new')
            expect(res).toEqual({ kind: 'forbidden' })
        })

        it('returns invalid on 422', async () => {
            vi.mocked(apiClient.put).mockRejectedValueOnce({
                response: { status: 422, data: { detail: { kind: 'not_a_dir' } } }
            })
            const res = await savePhotosDir('C:\\file')
            expect(res).toEqual({ kind: 'invalid', reason: 'not_a_dir' })
        })

        it('returns storage on 500', async () => {
            vi.mocked(apiClient.put).mockRejectedValueOnce({
                response: { status: 500, data: { detail: { kind: 'storage' } } }
            })
            const res = await savePhotosDir('C:\\new')
            expect(res).toEqual({ kind: 'storage' })
        })
    })
})
