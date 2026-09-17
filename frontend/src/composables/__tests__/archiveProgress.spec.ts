import { describe, it, expect } from 'vitest';
import {
    REJECTION_REASONS, SERVER_FAILURE_MESSAGES, SESSION_OUTCOMES,
    progressMessage, type ServerFailureReason
} from '../archiveProgress';

const serverFailures = [...SESSION_OUTCOMES, ...REJECTION_REASONS].filter(
    (reason): reason is ServerFailureReason => reason !== 'Completed'
);

describe('archive progress messages', () => {
    it('U12/V24: every server outcome except Completed has its own message, with no fallback', () => {
        expect(serverFailures).toContain('SourceChanged');
        expect(serverFailures).toContain('PreflightStalled');
        expect(Object.keys(SERVER_FAILURE_MESSAGES).sort()).toEqual([...serverFailures].sort());
        for (const reason of serverFailures) {
            const message = progressMessage({ kind: 'Failed', reason, filename: 'p.zip' });
            expect(message?.type).toBe('error');
            expect(message?.text).toBe(SERVER_FAILURE_MESSAGES[reason]);
            expect(message?.text).not.toContain(reason);
        }
    });

    it('U13: no abandon message describes a saved file', () => {
        for (const reason of ['AbandonedDisconnect', 'AbandonedBudget', 'AbandonedStall', 'FailedFraming', 'SourceChanged'] as const) {
            const text = SERVER_FAILURE_MESSAGES[reason];
            expect(text.toLowerCase()).not.toContain('saved');
            expect(text).toContain('Download again.');
        }
    });
});
