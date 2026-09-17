import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { usePhotoGallery, SLOW_POLL_MS } from '../usePhotoGallery';
import { progressMessage } from '../archiveProgress';
import * as photosApi from '../../api/photos';

vi.mock('../../api/photos', () => ({
    fetchPhotoFiles: vi.fn(),
    requestArchiveTicket: vi.fn(),
    fetchArchiveStatus: vi.fn(),
    archiveDownloadUrl: vi.fn().mockImplementation((t) => `mock_url/${t}`)
}));

type Status = Awaited<ReturnType<typeof photosApi.fetchArchiveStatus>>;

const readyFolder = {
    kind: 'ok' as const, status: 'ok' as const, folders: [], folders_truncated: false, truncated: false,
    entries: [{ name: '1.jpg', size_bytes: 10, mtime_ns: 0, version: '1', previewable: true }]
};

function statuses(...sequence: Status[]) {
    const mock = vi.mocked(photosApi.fetchArchiveStatus);
    mock.mockReset();
    let index = 0;
    mock.mockImplementation(async () => sequence[Math.min(index++, sequence.length - 1)]);
    return mock;
}

const terminal = (outcome: string, unresolved = 0): Status => ({
    state: 'Terminal', outcome, bytes_sent: 4096, entry_count: 1, unresolved_count: unresolved
});

describe('usePhotoGallery', () => {
    let gallery: ReturnType<typeof usePhotoGallery>;

    beforeEach(async () => {
        vi.useFakeTimers();
        document.body.innerHTML = '';
        gallery = usePhotoGallery();
        gallery.closeGallery();
        vi.mocked(photosApi.fetchPhotoFiles).mockResolvedValue(readyFolder);
        vi.mocked(photosApi.requestArchiveTicket).mockResolvedValue({ kind: 'ok', token: 'tok_123', filename: 'Photos.zip' });
        await gallery.openGallery('2023_01_01');
    });

    afterEach(() => {
        vi.useRealTimers();
    });

    it('opens gallery and loads entries', () => {
        expect(gallery.state.value.state).toBe('ready');
        if (gallery.state.value.state === 'ready') {
            expect(gallery.state.value.entries.length).toBe(1);
        }
    });

    it('handles toggle and clear selection', () => {
        gallery.toggleSelection('1.jpg');
        if (gallery.state.value.state === 'ready') {
            expect(gallery.state.value.selection.has('1.jpg')).toBe(true);
        }
        gallery.clearSelection();
        if (gallery.state.value.state === 'ready') {
            expect(gallery.state.value.selection.size).toBe(0);
        }
    });

    it('hands off to one reused iframe and keeps the selection', async () => {
        statuses(terminal('Completed'));
        gallery.toggleSelection('1.jpg');

        const first = gallery.downloadSelection();
        await vi.advanceTimersByTimeAsync(1000);
        await expect(first).resolves.toBeUndefined();

        const frame = document.getElementById('archive-download-frame') as HTMLIFrameElement;
        expect(frame.src).toContain('mock_url/tok_123');
        expect((gallery.state.value as any).selection.has('1.jpg')).toBe(true);

        const second = gallery.downloadSelection();
        await vi.advanceTimersByTimeAsync(1000);
        await second;
        expect(document.querySelectorAll('#archive-download-frame').length).toBe(1);
    });

    it('a LAN cap refusal fails at mint with its own message and creates no iframe', async () => {
        vi.mocked(photosApi.requestArchiveTicket).mockResolvedValueOnce({ kind: 'lan_cap_exceeded', limit: 'files' });
        await gallery.downloadSelection();
        const progress = gallery.downloadProgress.value;
        expect(progress).toEqual({ kind: 'Failed', reason: 'LanCapExceeded', filename: null });
        expect(progressMessage(progress)?.text).toContain('LAN bulk download limit exceeded');
        expect(document.getElementById('archive-download-frame')).toBeNull();
    });

    it('V23: Preparing, then Downloading, then a terminal outcome', async () => {
        statuses({ state: 'Pending' }, { state: 'Preparing' }, { state: 'Streaming' }, terminal('Completed'));
        const run = gallery.downloadSelection();
        await vi.advanceTimersByTimeAsync(0);
        expect(gallery.downloadProgress.value.kind).toBe('Preparing');
        await vi.advanceTimersByTimeAsync(2000);
        expect(gallery.downloadProgress.value.kind).toBe('Preparing');
        await vi.advanceTimersByTimeAsync(1000);
        expect(gallery.downloadProgress.value.kind).toBe('Downloading');
        await vi.advanceTimersByTimeAsync(1000);
        await run;
        expect(gallery.downloadProgress.value).toEqual({ kind: 'Succeeded', filename: 'Photos.zip', bytes: 4096, entries: 1, missing: 0 });
    });

    it('V23: SourceChanged fails with the reason', async () => {
        statuses(terminal('SourceChanged'));
        const run = gallery.downloadSelection();
        await vi.advanceTimersByTimeAsync(1000);
        await run;
        expect(gallery.downloadProgress.value).toEqual({ kind: 'Failed', reason: 'SourceChanged', filename: 'Photos.zip' });
    });

    it('U14: completion with missing files is distinguished from a clean save', async () => {
        statuses(terminal('Completed', 1));
        const run = gallery.downloadSelection();
        await vi.advanceTimersByTimeAsync(1000);
        await run;
        const progress = gallery.downloadProgress.value;
        expect(progress).toMatchObject({ kind: 'Succeeded', missing: 1 });
        expect(progressMessage(progress)?.text).toContain('_MISSING');
    });

    it('U16: Preparing past the fast window keeps polling at the slow cadence', async () => {
        const mock = statuses({ state: 'Preparing' });
        const run = gallery.downloadSelection();
        await vi.advanceTimersByTimeAsync(15_000);
        const callsInFastWindow = mock.mock.calls.length;
        expect(callsInFastWindow).toBe(15);

        await vi.advanceTimersByTimeAsync(SLOW_POLL_MS - 1);
        expect(mock.mock.calls.length).toBe(callsInFastWindow);
        await vi.advanceTimersByTimeAsync(1);
        expect(mock.mock.calls.length).toBe(callsInFastWindow + 1);
        expect(gallery.downloadProgress.value.kind).toBe('Preparing');

        mock.mockImplementation(async () => terminal('Completed'));
        await vi.advanceTimersByTimeAsync(SLOW_POLL_MS);
        await run;
        expect(gallery.downloadProgress.value.kind).toBe('Succeeded');
    });

    it('Unknown past the fast window is a lost session, not a failure', async () => {
        statuses({ state: 'Unknown' });
        const run = gallery.downloadSelection();
        await vi.advanceTimersByTimeAsync(15_000);
        await run;
        const progress = gallery.downloadProgress.value;
        expect(progress).toMatchObject({ kind: 'Failed', reason: 'PollAbandoned', lost: 'Unknown' });
        expect(progressMessage(progress)?.text).toContain('result is not known');
    });

    it('U11: a superseded poll is aborted in flight and emits nothing', async () => {
        const mock = vi.mocked(photosApi.fetchArchiveStatus);
        mock.mockReset();
        const signals: AbortSignal[] = [];
        mock.mockImplementationOnce((_token, signal) => new Promise<Status>((_resolve, reject) => {
            signals.push(signal!);
            signal!.addEventListener('abort', () => reject(Object.assign(new Error('canceled'), { name: 'CanceledError' })));
        }));
        mock.mockImplementation(async () => terminal('AbandonedBudget'));

        const first = gallery.downloadSelection();
        await vi.advanceTimersByTimeAsync(1000);
        expect(signals).toHaveLength(1);
        expect(signals[0].aborted).toBe(false);

        vi.mocked(photosApi.requestArchiveTicket).mockResolvedValueOnce({ kind: 'ok', token: 'tok_456', filename: 'Second.zip' });
        const second = gallery.downloadSelection();
        expect(signals[0].aborted).toBe(true);
        await first;

        await vi.advanceTimersByTimeAsync(1000);
        await second;
        expect(gallery.downloadProgress.value).toEqual({ kind: 'Failed', reason: 'AbandonedBudget', filename: 'Second.zip' });
    });
});
