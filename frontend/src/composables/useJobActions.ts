import { useHistoryStore } from '@/stores/history'
import { useShippingStore } from '@/stores/shipping'
import { editHistoryJob, discardHistoryJob, type HistoryEditDraft, type JobReadExpanded } from '@/api/history'

export type EditableField = 'part_number' | 'build_type' | 'split_suffix'
                   | 'repeat_reference' | 'build_qualifier'
                   | 'raw_qty' | 'raw_customer' | 'raw_shipped'

export type JobEditOutcome =
    | { kind: 'ok';         job: JobReadExpanded }
    | { kind: 'validation'; field: EditableField | null; message: string }
    | { kind: 'collision';  colliding_job_id: number }
    | { kind: 'conflict';   message: string }
    | { kind: 'network';    message: string }

export type JobDiscardOutcome =
    | { kind: 'ok';       job_id: number }
    | { kind: 'gone';     job_id: number; message: string }
    | { kind: 'conflict'; message: string }
    | { kind: 'network';  message: string }

export function canEdit(job: JobReadExpanded): boolean {
  return job.status === 'shipped' && job.discarded_at === null
}

export function canDiscard(job: JobReadExpanded): boolean {
  return job.discarded_at === null
}

export function classifyDiscardFailure(jobId: number, err: any): JobDiscardOutcome {
  const response = err?.response
  const status = response?.status
  const detail = response?.data?.detail

  if (status === 409 && detail && typeof detail === 'object' && detail.detail === 'Job is already discarded') {
    return { kind: 'gone', job_id: jobId, message: 'Job is already discarded' }
  }
  if (status === 409) {
    const msg = typeof detail === 'string' ? detail : 'Conflict'
    return { kind: 'conflict', message: msg }
  }
  if (status === 404 && detail === 'Job not found') {
    return { kind: 'gone', job_id: jobId, message: 'Job not found' }
  }
  return { kind: 'network', message: err instanceof Error ? err.message : 'Network error' }
}

function reconcileEdited(job: JobReadExpanded): void {
  const historyStore = useHistoryStore()
  const shippingStore = useShippingStore()
  historyStore.applyEdited(job)
  shippingStore.applyEdited(job)
}

function reconcileDiscarded(jobId: number): void {
  const historyStore = useHistoryStore()
  const shippingStore = useShippingStore()
  historyStore.applyDiscarded(jobId)
  shippingStore.applyDiscarded(jobId)
}

export function useJobActions() {
  async function editJob(
    job_id: number,
    edit: HistoryEditDraft,
    reason: string
  ): Promise<JobEditOutcome> {
    try {
      const refreshed = await editHistoryJob(job_id, edit, reason)
      reconcileEdited(refreshed)
      return { kind: 'ok', job: refreshed }
    } catch (err: any) {
      if (err.kind === 'validation') {
        return { kind: 'validation', field: err.field, message: err.message }
      }
      if (err.kind === 'collision') {
        return { kind: 'collision', colliding_job_id: err.collidingJobId }
      }
      if (err.kind === 'conflict') {
        return { kind: 'conflict', message: err.message }
      }
      if (err.kind === 'transport') {
        return { kind: 'network', message: err.message }
      }
      return { kind: 'network', message: String(err) }
    }
  }

  async function discardJob(job_id: number, reason: string): Promise<JobDiscardOutcome> {
    try {
      await discardHistoryJob(job_id, reason)
      reconcileDiscarded(job_id)
      return { kind: 'ok', job_id }
    } catch (err: any) {
      const outcome = classifyDiscardFailure(job_id, err)
      if (outcome.kind === 'gone') {
        reconcileDiscarded(job_id)
      }
      return outcome
    }
  }

  return { canEdit, canDiscard, editJob, discardJob }
}
