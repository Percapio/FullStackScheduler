<script setup lang="ts">
import { useRoute } from 'vue-router'
const route = useRoute()
</script>

<template>
  <div class="text-center text-slate-500 py-24">
    <p class="text-2xl font-light mb-2">{{ route.meta.label }}</p>
    <!-- <p class="text-sm">Coming in Phase {{ route.meta.phase }}</p> -->
    <p class="text-sm">(awaiting approval)</p>
  </div>
</template>
