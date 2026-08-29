import { ref } from 'vue'

export const INSPECT_VERBOSITY_STORAGE_KEY = 'inspect-drawer-show-all'

export type InspectVerbosity = boolean // true for flat, false for curated

export function useInspectVerbosity() {
  const showAllData = ref<InspectVerbosity>(false)

  try {
    const stored = window.localStorage.getItem(INSPECT_VERBOSITY_STORAGE_KEY)
    if (stored === 'true') {
      showAllData.value = true
    } else if (stored === 'false') {
      showAllData.value = false
    }
  } catch (e) {
    // Ignore blocked storage
  }

  const toggle = () => {
    const next = !showAllData.value
    showAllData.value = next
    try {
      window.localStorage.setItem(INSPECT_VERBOSITY_STORAGE_KEY, next ? 'true' : 'false')
    } catch (e) {
      // Ignore blocked storage
    }
  }

  return { showAllData, toggle }
}
