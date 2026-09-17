// The archive download's outcome vocabulary and its operator-facing messages
// (Phase 31 §6.2, Phase 32 §11). The two server lists mirror
// backend/app/services/archive_status.py; tests/test_archive_outcomes_published.py
// fails the backend suite if they drift, and archiveProgress.spec.ts fails if any
// member lacks a message.

export const SESSION_OUTCOMES = [
    'Completed', 'AbandonedDisconnect', 'AbandonedBudget',
    'AbandonedStall', 'FailedFraming', 'TicketRefused',
    'FailedStart', 'FolderNotFound', 'ListingUnavailable',
    'SourceChanged', 'PreflightStalled',
] as const;

export const REJECTION_REASONS = [
    'TokenExpired', 'TokenSpent', 'TokenScope',
    'PermitsExhausted', 'ReaderBacklog',
] as const;

export type SessionOutcome = typeof SESSION_OUTCOMES[number];
export type RejectionReason = typeof REJECTION_REASONS[number];
export type TerminalReason = SessionOutcome | RejectionReason;
export type ServerFailureReason = Exclude<TerminalReason, 'Completed'>;

// Client-originated reasons have no server constant behind them, so they are
// kept out of the schema-mirrored lists above.
export type PollLoss = 'Unknown' | 'GaveUp';
export type MintFailureReason = 'LanCapExceeded' | 'NetworkError';
export type FailureReason = ServerFailureReason | MintFailureReason | 'PollAbandoned';

export type DownloadProgress =
    | { kind: 'Idle' }
    | { kind: 'Minting' }
    | { kind: 'Preparing'; filename: string }
    | { kind: 'Downloading'; filename: string }
    | { kind: 'Failed'; reason: FailureReason; filename: string | null; lost?: PollLoss }
    | { kind: 'Succeeded'; filename: string; bytes: number; entries: number; missing: number };

export const SERVER_FAILURE_MESSAGES: Record<ServerFailureReason, string> = {
    PermitsExhausted: 'Another download is in progress. Try again in a few seconds.',
    ReaderBacklog: 'Another download is in progress. Try again in a few seconds.',
    TokenExpired: 'The download link expired. Select the photos again.',
    TokenSpent: 'That download has already been used. Select the photos again.',
    TokenScope: 'This download can only be started from the machine that created it.',
    TicketRefused: 'The download link expired. Select the photos again.',
    FolderNotFound: 'The photo folder is no longer available.',
    ListingUnavailable: 'The photo folder is no longer available.',
    FailedStart: 'The server could not start the download. Nothing was saved.',
    AbandonedDisconnect: 'The download was interrupted before it finished. Download again.',
    AbandonedBudget: 'The download took too long and was stopped before it finished. Download again.',
    AbandonedStall: 'The download failed on the server before it finished. Download again.',
    FailedFraming: 'The download failed on the server before it finished. Download again.',
    SourceChanged: 'The photos changed while the download was running. Download again.',
    PreflightStalled: 'The photo folder is not responding. Try again in a minute.',
};

const MINT_FAILURE_MESSAGES: Record<MintFailureReason, string> = {
    LanCapExceeded: 'LAN bulk download limit exceeded. Select fewer photos, or download from the machine running Scheduler.',
    NetworkError: 'The server could not be reached. Try again.',
};

const POLL_LOSS_MESSAGES: Record<PollLoss, string> = {
    Unknown: 'The download was started but its result is not known. Check your Downloads folder.',
    GaveUp: 'The download is still running or the server stopped responding. Check your Downloads folder.',
};

export type ProgressMessage = { type: 'info' | 'error' | 'success'; text: string };

export function isServerFailureReason(reason: string): reason is ServerFailureReason {
    return Object.prototype.hasOwnProperty.call(SERVER_FAILURE_MESSAGES, reason);
}

export function formatBytes(bytes: number): string {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.min(Math.floor(Math.log(bytes) / Math.log(k)), sizes.length - 1);
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

export function failureMessage(reason: FailureReason, lost?: PollLoss): string {
    if (reason === 'PollAbandoned') return POLL_LOSS_MESSAGES[lost ?? 'GaveUp'];
    if (reason === 'LanCapExceeded' || reason === 'NetworkError') return MINT_FAILURE_MESSAGES[reason];
    return SERVER_FAILURE_MESSAGES[reason];
}

export function progressMessage(progress: DownloadProgress): ProgressMessage | null {
    switch (progress.kind) {
        case 'Idle':
            return null;
        case 'Minting':
        case 'Preparing':
            return { type: 'info', text: 'Preparing the download…' };
        case 'Downloading':
            return { type: 'info', text: "Download started — check your browser's downloads." };
        case 'Failed':
            return { type: 'error', text: failureMessage(progress.reason, progress.lost) };
        case 'Succeeded':
            if (progress.missing === 0) {
                return { type: 'success', text: `Saved. (${formatBytes(progress.bytes)})` };
            }
            return {
                type: 'success',
                text: `Saved, but ${progress.missing} files could not be included. The archive lists them in _MISSING.`,
            };
    }
}
