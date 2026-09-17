import type { ReviewGroup } from '@/api/review'

/**
 * Returns a stable composite key for a `ReviewGroup` that is unique across
 * all intra_file_duplicates groups within a single batch.
 *
 * Two groups may share the same `parsed_part_number` but differ by build type,
 * split suffix, repeat reference, or build qualifier — each combination
 * constitutes a distinct identity and must receive a distinct Vue `:key`.
 *
 * `origin` is part of the key (Patch 06 §5.5): an `edit_induced` group whose
 * effective identity equals a `staged` group's raw identity would otherwise
 * produce a duplicate key.
 */
export function intraFileDuplicateKey(group: ReviewGroup): string {
  return JSON.stringify({
    origin: group.origin ?? null,
    part_number: group.identity?.part_number ?? group.parsed_part_number,
    build_type: group.identity?.build_type ?? null,
    split_suffix: group.identity?.split_suffix ?? null,
    repeat_reference: group.identity?.repeat_reference ?? null,
    build_qualifier: group.identity?.build_qualifier ?? null,
  })
}
