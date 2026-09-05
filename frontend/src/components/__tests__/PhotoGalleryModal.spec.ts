import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { nextTick, ref } from 'vue'
import PhotoGalleryModal from '../PhotoGalleryModal.vue'

// Patch 05 section 8.4.

type Entry = { name: string; size_bytes: number; mtime_ns: number; version: string; previewable: boolean }

function entry(name: string, previewable = true, version = 'v1'): Entry {
    return { name, size_bytes: 10, mtime_ns: 1, version, previewable }
}

function makeGallery(date_folder: string, entries: Entry[]) {
    const state = ref<any>({
        state: 'ready',
        date_folder,
        sub_folder: '',
        folders: [],
        entries,
        truncated: false,
        folders_truncated: false,
        selection: new Set<string>()
    })
    return {
        state,
        openGallery: vi.fn(),
        closeGallery: vi.fn(),
        toggleSelection: vi.fn(),
        selectAll: vi.fn(),
        clearSelection: vi.fn(),
        downloadSelection: vi.fn().mockResolvedValue(null)
    }
}

function mountGallery(gallery: any): VueWrapper<any> {
    return mount(PhotoGalleryModal, {
        props: { gallery: gallery as any },
        global: { stubs: { Teleport: true } }
    })
}

describe('PhotoGalleryModal', () => {
    beforeEach(() => {
        vi.useFakeTimers()
    })

    afterEach(() => {
        vi.useRealTimers()
    })

    it('an error event renders the placeholder and schedules nothing', async () => {
        const gallery = makeGallery('2023_01_01', [entry('a.jpg')])
        const wrapper = mountGallery(gallery)

        expect(wrapper.findAll('img')).toHaveLength(1)

        await wrapper.get('img').trigger('error')
        expect(wrapper.findAll('img')).toHaveLength(0)
        expect(wrapper.text()).toContain('Click to retry')

        // The deleted backoff would have re-requested by now. Nothing may.
        await vi.advanceTimersByTimeAsync(60_000)
        await nextTick()
        expect(wrapper.findAll('img')).toHaveLength(0)
    })

    it('a tile src carries no retry parameter and is stable across renders', async () => {
        const gallery = makeGallery('2023_01_01', [entry('a.jpg')])
        const wrapper = mountGallery(gallery)

        const first = wrapper.get('img').attributes('src')!
        expect(first).not.toContain('retry')

        // Re-render the same entry; the URL must be byte-identical, or the
        // browser's immutable caching never applies.
        gallery.state.value = { ...gallery.state.value, selection: new Set(['a.jpg']) }
        await nextTick()
        expect(wrapper.get('img').attributes('src')).toBe(first)
    })

    it('no img carries loading="lazy"', () => {
        const gallery = makeGallery('2023_01_01', [entry('a.jpg'), entry('b.jpg'), entry('c.jpg')])
        const wrapper = mountGallery(gallery)

        const imgs = wrapper.findAll('img')
        expect(imgs).toHaveLength(3)
        for (const img of imgs) {
            expect(img.attributes('loading')).toBeUndefined()
        }
    })

    it('pending tiles render the skeleton and the header counter clears when all resolve', async () => {
        const gallery = makeGallery('2023_01_01', [entry('a.jpg'), entry('b.jpg')])
        const wrapper = mountGallery(gallery)

        expect(wrapper.findAll('.animate-pulse')).toHaveLength(2)
        expect(wrapper.text()).toContain('0 of 2 ready')

        await wrapper.findAll('img')[0].trigger('load')
        expect(wrapper.text()).toContain('1 of 2 ready')
        expect(wrapper.findAll('.animate-pulse')).toHaveLength(1)

        await wrapper.findAll('img')[1].trigger('load')
        expect(wrapper.text()).not.toContain('ready')
        expect(wrapper.findAll('.animate-pulse')).toHaveLength(0)
    })

    it('a failed tile counts as resolved', async () => {
        const gallery = makeGallery('2023_01_01', [entry('a.jpg'), entry('b.jpg')])
        const wrapper = mountGallery(gallery)

        await wrapper.findAll('img')[0].trigger('load')
        expect(wrapper.text()).toContain('1 of 2 ready')

        // One corrupt photo must not park the counter at 1 of 2.
        await wrapper.findAll('img')[1].trigger('error')
        expect(wrapper.text()).not.toContain('ready')
    })

    it('clicking a failed placeholder issues exactly one new request at the same URL', async () => {
        const gallery = makeGallery('2023_01_01', [entry('a.jpg')])
        const wrapper = mountGallery(gallery)

        const originalUrl = wrapper.get('img').attributes('src')!
        await wrapper.get('img').trigger('error')
        expect(wrapper.findAll('img')).toHaveLength(0)

        await wrapper.get('[class*="cursor-pointer"] div.absolute.inset-0.flex').trigger('click')

        const retried = wrapper.findAll('img')
        expect(retried).toHaveLength(1)
        // Same URL, new element -- a nonce appended to src would defeat the
        // immutable caching the retry parameter's removal restored.
        expect(retried[0].attributes('src')).toBe(originalUrl)
    })

    it('a load event from the previous folder is discarded', async () => {
        const gallery = makeGallery('2023_01_01', [entry('a.jpg'), entry('b.jpg')])
        const wrapper = mountGallery(gallery)

        const staleImg = wrapper.findAll('img')[0]

        gallery.state.value = {
            state: 'ready',
            date_folder: '2023_02_02',
            sub_folder: '',
            folders: [],
            entries: [entry('x.jpg'), entry('y.jpg')],
            truncated: false,
            folders_truncated: false,
            selection: new Set<string>()
        }
        await nextTick()
        expect(wrapper.text()).toContain('0 of 2 ready')

        // A's response lands after the switch. Clearing the sets on change does
        // not help -- the stale event arrives after the clear.
        await staleImg.trigger('load')

        expect(wrapper.text()).toContain('0 of 2 ready')
        expect(wrapper.findAll('.animate-pulse')).toHaveLength(2)
    })

    it('two folders sharing a filename do not reuse a tile DOM node', async () => {
        const gallery = makeGallery('2023_01_01', [entry('IMG_0001.jpg')])
        const wrapper = mountGallery(gallery)

        const before = wrapper.get('img').element

        gallery.state.value = {
            state: 'ready',
            date_folder: '2023_02_02',
            sub_folder: '',
            folders: [],
            entries: [entry('IMG_0001.jpg')],
            truncated: false,
            folders_truncated: false,
            selection: new Set<string>()
        }
        await nextTick()

        const after = wrapper.get('img').element
        expect(after).not.toBe(before)
        expect(after.getAttribute('src')).toContain('2023_02_02')
    })

    it('renders the download button label', () => {
        const gallery = makeGallery('2023_01_01', [entry('a.jpg')])
        const wrapper = mountGallery(gallery)

        const labels = wrapper.findAll('button').map(b => b.text())
        expect(labels).toContain('Download')
    })
})
