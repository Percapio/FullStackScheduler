import { marked } from 'marked'
import type { JobReadExpanded } from '@/api/history'

export type CarrierBadge = { text: string; class: string } | null

const CARRIER_CLASSES = {
  fedex: 'text-xs font-semibold uppercase tracking-wider ' +
         'bg-purple-100 text-purple-800 border-purple-300 ' +
         'dark:bg-purple-900/40 dark:text-purple-200 dark:border-purple-700',
  ups:   'text-xs font-semibold uppercase tracking-wider ' +
         'bg-amber-100 text-amber-900 border-amber-400 ' +
         'dark:bg-amber-900/40 dark:text-amber-200 dark:border-amber-700',
  other: 'text-xs font-semibold uppercase tracking-wider ' +
         'bg-slate-100 text-slate-700 border-slate-300 ' +
         'dark:bg-slate-700/60 dark:text-slate-200 dark:border-slate-600',
} as const

export function useJobFormatters() {
  function formatDate(iso: string | null | undefined): string {
    if (!iso) return '—'
    return iso.slice(0, 10)
  }

  function buildLabel(bt: string | null | undefined): string {
    if (!bt || bt === 'new') return ''
    return bt.toUpperCase()
  }

  function identitySuffix(job: JobReadExpanded): string {
    const parts: string[] = []
    if (job.split_suffix) parts.push(job.split_suffix)
    if (job.repeat_reference) parts.push(`RONC ${job.repeat_reference}`)
    return parts.length ? ' ' + parts.join(' · ') : ''
  }

  function renderNotes(raw: string | null | undefined): string {
    if (!raw) return ''
    return marked.parse(raw, { async: false, gfm: true, breaks: true }) as string
  }

  function carrierBadge(raw: string | null | undefined): CarrierBadge {
    if (!raw) return null
    const key = raw.trim().toLowerCase().replace(/[\s\-_]/g, '')
    if (!key) return null
    const display = raw.replace(/\s+/g, ' ').trim()
    if (key.startsWith('fedex')) return { text: display, class: CARRIER_CLASSES.fedex }
    if (key.startsWith('ups'))   return { text: display, class: CARRIER_CLASSES.ups }
    return { text: display, class: CARRIER_CLASSES.other }
  }

  return { formatDate, buildLabel, identitySuffix, renderNotes, carrierBadge }
}
