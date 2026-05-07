import { describe, it, expect, afterEach } from 'vitest'
import { mount, flushPromises, VueWrapper } from '@vue/test-utils'
import RestoreConflictPreview from '../RestoreConflictPreview.vue'
import type { RestoreConflictPreview as PreviewPayload, StagingRowDetail } from '@/api/staging'

const TS = '2026-05-01T12:00:00Z'

function makeDetail(id: number, raw_job = 'ABC-12345 NEW'): StagingRowDetail {
  return {
    id,
    batch_id: 1,
    source_row_number: id,
    processing_status: 'error',
    processing_error: 'test error',
    suggested_correction: null,
    resolved_job_id: null,
    processed_at: null,
    discarded_at: null,
    created_at: TS,
    updated_at: TS,
    raw_job,
    raw_qty: null,
    raw_ship_date: null,
    raw_prog: null,
    raw_customer: null,
    raw_sales_p: null,
    raw_shipped: null,
    raw_pcb_notes: null,
    raw_kit_notes: null,
    raw_scheduling_notes: null,
    raw_line_1: null,
    raw_line_2: null,
    raw_line_3: null,
    raw_mfg_notes: null,
    raw_smt_lines: null,
    raw_smt_plcmnts: null,
    raw_ship_method: null,
    raw_doc_rel: null,
    raw_kit_rel: null,
    raw_code: null,
    raw_bom_compare_photos: null,
    duplicate_group_key: null,
    build_qualifier: null,
    highlight_fields: [],
  } as StagingRowDetail
}

function emptyPreview(): PreviewPayload {
  return {
    incoming: {
      kind: 'staging',
      staging: makeDetail(10),
      job: null,
    },
    colliding_staging_errored_rows: [],
    colliding_staging_discarded_rows: [],
    colliding_live_jobs: [],
    group_key: 'ABC-12345|new|||',
  }
}

let wrapper: VueWrapper | null = null

function mountComponent(props: {
  open?: boolean
  preview?: PreviewPayload | null
  submitting?: boolean
}) {
  wrapper = mount(RestoreConflictPreview, {
    attachTo: document.body,
    props: {
      open: props.open ?? true,
      preview: props.preview ?? emptyPreview(),
      submitting: props.submitting ?? false,
    },
  })
  return wrapper
}

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

describe('RestoreConflictPreview', () => {
  describe('closed state', () => {
    it('renders nothing when open is false', () => {
      mountComponent({ open: false })
      expect(document.body.querySelector('[data-testid="restore-conflict-overlay"]')).toBeNull()
    })
  })

  describe('open state with no colliders', () => {
    it('renders the overlay when open and preview are provided', () => {
      mountComponent({ open: true })
      expect(document.body.querySelector('[data-testid="restore-conflict-overlay"]')).not.toBeNull()
    })

    it('shows the incoming row', () => {
      mountComponent({ open: true, preview: emptyPreview() })
      const incoming = document.body.querySelector('[data-testid="restore-conflict-incoming"]')
      expect(incoming).not.toBeNull()
      expect(incoming!.textContent).toContain('10')
    })

    it('does not render errored collider section when list is empty', () => {
      mountComponent({ open: true, preview: emptyPreview() })
      expect(document.body.querySelector('[data-testid^="restore-conflict-errored-"]')).toBeNull()
    })

    it('does not render live-job section when list is empty', () => {
      mountComponent({ open: true, preview: emptyPreview() })
      expect(document.body.querySelector('[data-testid^="restore-conflict-live-job-"]')).toBeNull()
    })

    it('Restore button is disabled when no drafts are pending', () => {
      mountComponent({ open: true, preview: emptyPreview() })
      const btn = document.body.querySelector('[data-testid="restore-conflict-restore-btn"]') as HTMLButtonElement
      expect(btn.disabled).toBe(true)
    })
  })

  describe('errored colliders (class i)', () => {
    function previewWithErrored(): PreviewPayload {
      return {
        ...emptyPreview(),
        colliding_staging_errored_rows: [makeDetail(20), makeDetail(21)],
      }
    }

    it('renders a row for each errored collider', () => {
      mountComponent({ open: true, preview: previewWithErrored() })
      expect(document.body.querySelector('[data-testid="restore-conflict-errored-20"]')).not.toBeNull()
      expect(document.body.querySelector('[data-testid="restore-conflict-errored-21"]')).not.toBeNull()
    })

    it('renders a discard checkbox for each errored collider', () => {
      mountComponent({ open: true, preview: previewWithErrored() })
      const row = document.body.querySelector('[data-testid="restore-conflict-errored-20"]')
      const checkbox = row?.querySelector('input[type="checkbox"]') as HTMLInputElement | null
      expect(checkbox).not.toBeNull()
    })

    it('Restore button becomes enabled after checking a collider', async () => {
      mountComponent({ open: true, preview: previewWithErrored() })
      const row = document.body.querySelector('[data-testid="restore-conflict-errored-20"]')
      const checkbox = row?.querySelector('input[type="checkbox"]') as HTMLInputElement
      checkbox.click()
      await flushPromises()
      const btn = document.body.querySelector('[data-testid="restore-conflict-restore-btn"]') as HTMLButtonElement
      expect(btn.disabled).toBe(false)
    })

    it('emitting restore with discard action when Restore button is clicked', async () => {
      const w = mountComponent({ open: true, preview: previewWithErrored() })
      const row = document.body.querySelector('[data-testid="restore-conflict-errored-20"]')
      const checkbox = row?.querySelector('input[type="checkbox"]') as HTMLInputElement
      checkbox.click()
      await flushPromises()
      const btn = document.body.querySelector('[data-testid="restore-conflict-restore-btn"]') as HTMLButtonElement
      btn.click()
      await flushPromises()
      const emitted = w.emitted('restore')
      expect(emitted).not.toBeNull()
      expect(emitted![0][0]).toEqual({ actions: [{ kind: 'discard', row_id: 20 }] })
    })

    it('unchecking a previously checked collider re-disables Restore', async () => {
      mountComponent({ open: true, preview: previewWithErrored() })
      const row = document.body.querySelector('[data-testid="restore-conflict-errored-20"]')
      const checkbox = row?.querySelector('input[type="checkbox"]') as HTMLInputElement
      // Check then uncheck
      checkbox.click()
      await flushPromises()
      checkbox.click()
      await flushPromises()
      const btn = document.body.querySelector('[data-testid="restore-conflict-restore-btn"]') as HTMLButtonElement
      expect(btn.disabled).toBe(true)
    })
  })

  describe('discarded colliders (class ii)', () => {
    function previewWithDiscarded(): PreviewPayload {
      return {
        ...emptyPreview(),
        colliding_staging_discarded_rows: [makeDetail(30)],
      }
    }

    it('renders discarded colliders', () => {
      mountComponent({ open: true, preview: previewWithDiscarded() })
      expect(document.body.querySelector('[data-testid="restore-conflict-discarded-30"]')).not.toBeNull()
    })

    it('does not render a discard checkbox for discarded colliders', () => {
      mountComponent({ open: true, preview: previewWithDiscarded() })
      const row = document.body.querySelector('[data-testid="restore-conflict-discarded-30"]')
      const checkbox = row?.querySelector('input[type="checkbox"]')
      expect(checkbox).toBeNull()
    })

    it('Restore button remains disabled when only discarded colliders exist and none errored', () => {
      mountComponent({ open: true, preview: previewWithDiscarded() })
      const btn = document.body.querySelector('[data-testid="restore-conflict-restore-btn"]') as HTMLButtonElement
      expect(btn.disabled).toBe(true)
    })
  })

  describe('live-job colliders (class iii)', () => {
    function previewWithLiveJob(): PreviewPayload {
      return {
        ...emptyPreview(),
        colliding_live_jobs: [{ id: 77 } as never],
      }
    }

    it('renders live-job colliders', () => {
      mountComponent({ open: true, preview: previewWithLiveJob() })
      expect(document.body.querySelector('[data-testid="restore-conflict-live-job-77"]')).not.toBeNull()
    })

    it('shows Resolve in History link for live jobs', () => {
      mountComponent({ open: true, preview: previewWithLiveJob() })
      const link = document.body.querySelector('[data-testid="restore-conflict-history-link"]')
      expect(link).not.toBeNull()
    })

    it('Restore button is disabled even with an errored collider checked when live jobs exist', async () => {
      mountComponent({
        open: true,
        preview: {
          ...emptyPreview(),
          colliding_staging_errored_rows: [makeDetail(20)],
          colliding_live_jobs: [{ id: 77 } as never],
        },
      })
      const row = document.body.querySelector('[data-testid="restore-conflict-errored-20"]')
      const checkbox = row?.querySelector('input[type="checkbox"]') as HTMLInputElement
      checkbox.click()
      await flushPromises()
      const btn = document.body.querySelector('[data-testid="restore-conflict-restore-btn"]') as HTMLButtonElement
      expect(btn.disabled).toBe(true)
    })
  })

  describe('cancel behaviour', () => {
    it('emits cancel when the × button is clicked', async () => {
      const w = mountComponent({ open: true })
      const btn = document.body.querySelector('[data-testid="restore-conflict-cancel-btn"]') as HTMLButtonElement
      btn.click()
      await flushPromises()
      expect(w.emitted('cancel')).not.toBeNull()
    })

    it('emits cancel when the footer Cancel button is clicked', async () => {
      const w = mountComponent({ open: true })
      const btn = document.body.querySelector('[data-testid="restore-conflict-cancel-footer-btn"]') as HTMLButtonElement
      btn.click()
      await flushPromises()
      expect(w.emitted('cancel')).not.toBeNull()
    })
  })

  describe('submitting state', () => {
    it('disables all buttons when submitting', () => {
      mountComponent({ open: true, submitting: true })
      const restoreBtn = document.body.querySelector('[data-testid="restore-conflict-restore-btn"]') as HTMLButtonElement
      const cancelBtn = document.body.querySelector('[data-testid="restore-conflict-cancel-btn"]') as HTMLButtonElement
      expect(restoreBtn.disabled).toBe(true)
      expect(cancelBtn.disabled).toBe(true)
    })

    it('shows "Restoring…" text on the restore button when submitting', () => {
      mountComponent({ open: true, submitting: true })
      const btn = document.body.querySelector('[data-testid="restore-conflict-restore-btn"]')
      expect(btn!.textContent?.trim()).toContain('Restoring')
    })

    it('disables checkboxes when submitting', () => {
      mountComponent({
        open: true,
        submitting: true,
        preview: {
          ...emptyPreview(),
          colliding_staging_errored_rows: [makeDetail(20)],
        },
      })
      const row = document.body.querySelector('[data-testid="restore-conflict-errored-20"]')
      const checkbox = row?.querySelector('input[type="checkbox"]') as HTMLInputElement
      expect(checkbox.disabled).toBe(true)
    })
  })
})
