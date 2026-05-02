import { ref, computed } from 'vue'

const FONT_SIZES = ['text-xs', 'text-sm', 'text-base', 'text-lg', 'text-xl'] as const
const STORAGE_KEY = 'shipping-font-size'

function loadIndex(): number {
  const stored = localStorage.getItem(STORAGE_KEY)
  if (stored !== null) {
    const n = Number(stored)
    if (Number.isInteger(n) && n >= 0 && n < FONT_SIZES.length) return n
  }
  return 2
}

const fontSizeIndex = ref(loadIndex())

export function useFontSize() {
  const fontClass = computed(() => FONT_SIZES[fontSizeIndex.value])
  const canDecrease = computed(() => fontSizeIndex.value > 0)
  const canIncrease = computed(() => fontSizeIndex.value < FONT_SIZES.length - 1)

  function decrease() {
    if (canDecrease.value) {
      fontSizeIndex.value--
      localStorage.setItem(STORAGE_KEY, String(fontSizeIndex.value))
    }
  }

  function increase() {
    if (canIncrease.value) {
      fontSizeIndex.value++
      localStorage.setItem(STORAGE_KEY, String(fontSizeIndex.value))
    }
  }

  return { fontClass, canDecrease, canIncrease, decrease, increase }
}
