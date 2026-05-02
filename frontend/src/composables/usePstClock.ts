import { ref, onMounted, onUnmounted } from 'vue'

export function usePstClock() {
  const formatter = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/Los_Angeles',
    hour: '2-digit',
    minute: '2-digit',
    hour12: true,
  })

  const time = ref(formatter.format(new Date()))
  let timer: ReturnType<typeof setInterval> | null = null

  function tick() {
    time.value = formatter.format(new Date())
  }

  onMounted(() => {
    tick()
    timer = setInterval(tick, 60_000)
  })

  onUnmounted(() => {
    if (timer) clearInterval(timer)
  })

  return { time }
}
