import type { JobReadExpanded } from '@/api/history'

export interface Clock {
  currentYear(): number
  /** Today in the shop's timezone, as `YYYY-MM-DD`. */
  currentDate(): string
}

/** The shop floor reads one clock; the nav displays the same zone. A job ships
 *  "today" by Pacific reckoning, not by the viewer's browser locale. */
const SHOP_TIME_ZONE = 'America/Los_Angeles'

const shopDateFormatter = new Intl.DateTimeFormat('en-CA', {
  timeZone: SHOP_TIME_ZONE,
  year: 'numeric', month: '2-digit', day: '2-digit',
})

export const systemClock: Clock = {
  currentYear: () => (new Date).getFullYear(),
  currentDate: () => shopDateFormatter.format(new Date()),
}

const ISO_SEPARATOR = '-'
const DISPLAY_SEPARATOR = '/'
const EM_DASH = '—'

function toDisplaySeparators(isoFragment: string): string {
  return isoFragment.split(ISO_SEPARATOR).join(DISPLAY_SEPARATOR)
}

export function useJobFormatters(clock: Clock = systemClock) {
  function formatDate(iso: string | null | undefined): string {
    if (!iso) return EM_DASH
    return toDisplaySeparators(iso.slice(0, 10))
  }

  function formatShortDate(iso: string | null | undefined): string {
    if (!iso) return EM_DASH
    const monthDay = toDisplaySeparators(iso.slice(5, 10))
    if (iso.slice(0, 4) === String(clock.currentYear())) {
      return monthDay
    }
    return `${monthDay}${DISPLAY_SEPARATOR}${iso.slice(2, 4)}`
  }

  function isShippingToday(iso: string | null | undefined): boolean {
    if (!iso) return false
    return iso.slice(0, 10) === clock.currentDate()
  }

  function buildLabel(bt: string | null | undefined): string {
    if (!bt || bt === 'new') return ''
    return bt.toUpperCase()
  }

  function identitySuffix(job: JobReadExpanded): string {
    return job.split_suffix ? ' ' + job.split_suffix : ''
  }

  function renderNotes(raw: string | null | undefined): string {
    if (!raw) return ''

    let text = raw.replace(/~~[\s\S]*?~~/g, '')
    text = text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    text = text.replace(/\*/g, '')

    const lines = text.split('\n').map(l => l.trim()).filter(l => l.length > 0)
    if (lines.length === 0) return ''

    const listItems = lines.map(l => `<li>${l}</li>`).join('')
    return `<div class="font-medium text-slate-800 dark:text-slate-100"><ul class="list-disc pl-5">${listItems}</ul></div>`
  }

  /** Compose the operator-visible label for a job.
   *
   * Format: `${partNumber}${identitySuffix(job)}[ ${buildLabel(build_type)}]`
   * Matches what operators read in the workbook (audit #16).
   */
  function jobLabel(partNumber: string, job: JobReadExpanded): string {
    const parts: string[] = []
    if (job.split_suffix) parts.push(job.split_suffix)
    if (job.repeat_reference) parts.push(`RONC ${job.repeat_reference}`)
    const suffix = parts.length ? ' ' + parts.join(' · ') : ''
    const build  = buildLabel(job.build_type)
    return `${partNumber}${suffix}${build ? ' ' + build : ''}`
  }

  return { formatDate, formatShortDate, isShippingToday, buildLabel, identitySuffix, jobLabel, renderNotes }
}
