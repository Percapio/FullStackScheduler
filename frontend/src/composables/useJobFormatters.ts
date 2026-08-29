import type { JobReadExpanded } from '@/api/history'

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
    const suffix = identitySuffix(job)
    const build  = buildLabel(job.build_type)
    return `${partNumber}${suffix}${build ? ' ' + build : ''}`
  }

  return { formatDate, buildLabel, identitySuffix, jobLabel, renderNotes }
}
