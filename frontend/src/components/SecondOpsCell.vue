<script setup lang="ts">
/**
 * SecondOpsCell — the tri-state 2nd OPS grid cell, shared by Shipping and History.
 *
 * Three constraints here are not style preferences:
 *
 *  1. TEXT INTERPOLATION ONLY. Descriptions originate in an operator's
 *     spreadsheet and cross a trust boundary. The neighbouring Mfg Notes cell
 *     pipes its content through the Markdown helper in useJobFormatters and
 *     renders the result as raw HTML; reaching for that helper here by habit
 *     would make every pasted BOM an injection vector. The rule is enforced in
 *     src/__tests__/secondOpsGuards.spec.ts, not just asserted here.
 *  2. @click.stop ON EVERY INTERACTIVE ELEMENT. HistoryView's <tr> carries
 *     @click="toggleExpand", so without .stop every item click also toggles the
 *     lineage accordion.
 *  3. summary === null RENDERS NOTHING AT ALL, not `unaudited`. Only the two
 *     grid endpoints populate it; treating null as "never audited" would assert
 *     it about jobs from every other endpoint.
 */
import { computed } from 'vue'
import type { AuditBomFields, SecondOpsLine, SecondOpsSummary } from '@/api/secondOps'

const props = defineProps<{
  summary: SecondOpsSummary | null
  /** History is the audit trail: read-only, frozen at ship. No Audit, no EDIT. */
  readonly?: boolean
}>()

const emit = defineEmits<{
  audit: []
  inspect: [fields: AuditBomFields]
  viewAll: []
}>()

const state = computed(() => props.summary?.state ?? null)

const hasUnshownLines = computed(() => {
  const summary = props.summary
  if (summary === null) return false
  return summary.line_count > (summary.preview ?? []).length && summary.line_count > 0
})

const hasUnexpectedInclusions = computed(() => {
  const summary = props.summary
  if (summary === null) return false
  return summary.has_unexpected_inclusions
})

function previewLabel(line: SecondOpsLine): string {
  const f = (line.find_number ?? '').trim()
  return f ? '#' + f : '—'
}

type Absent = undefined
function previewTooltip(line: SecondOpsLine): string | Absent {
  const d = (line.description ?? '').trim()
  return d ? d : undefined
}
</script>

<template>
  <div v-if="summary !== null" class="flex flex-col gap-1" data-testid="second-ops-cell">
    <template v-if="state === 'unaudited'">
      <span v-if="readonly" class="text-slate-400 dark:text-slate-500">—</span>
      <button
        v-else
        type="button"
        data-testid="second-ops-audit-btn"
        class="self-start rounded px-2 py-0.5 text-xs font-medium border border-slate-300 dark:border-slate-600
               text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors"
        @click.stop="emit('audit')"
      >
        Audit
      </button>
    </template>

    <template v-else-if="state === 'not_applicable'">
      <div class="flex items-center gap-2">
        <span class="text-slate-500 dark:text-slate-400" data-testid="second-ops-na">N/A</span>
        <button
          v-if="!readonly"
          type="button"
          data-testid="second-ops-edit-btn"
          class="rounded px-2 py-0.5 text-xs font-medium border border-slate-300 dark:border-slate-600
                 text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors"
          @click.stop="emit('audit')"
        >
          EDIT
        </button>
      </div>
    </template>

    <template v-else-if="state === 'recorded'">
      <ul class="space-y-0.5">
        <li v-for="line in (summary.preview ?? [])" :key="line.id">
          <button
            type="button"
            :title="previewTooltip(line)"
            data-testid="second-ops-preview-line"
            class="text-left text-slate-700 dark:text-slate-300 hover:underline focus-visible:outline-none
                   focus-visible:ring-2 focus-visible:ring-blue-500/70 rounded"
            @click.stop="emit('inspect', line)"
          >{{ previewLabel(line) }}</button>
        </li>
      </ul>
      <div class="flex items-center gap-2">
        <button
          v-if="hasUnshownLines"
          type="button"
          data-testid="second-ops-view-all-btn"
          class="text-xs font-medium text-blue-600 dark:text-blue-400 hover:underline"
          @click.stop="emit('viewAll')"
        >
          View all ({{ summary.line_count }})
        </button>
        <button
          v-if="hasUnexpectedInclusions"
          type="button"
          data-testid="second-ops-adds-btn"
          class="text-xs font-medium text-blue-600 dark:text-blue-400 hover:underline"
          @click.stop="emit('viewAll')"
        >
          adds
        </button>
        <button
          v-if="!readonly"
          type="button"
          data-testid="second-ops-edit-btn"
          class="rounded px-2 py-0.5 text-xs font-medium border border-slate-300 dark:border-slate-600
                 text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors"
          @click.stop="emit('audit')"
        >
          EDIT
        </button>
      </div>
    </template>
  </div>
</template>
