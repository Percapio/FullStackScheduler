<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import AppNav from '@/components/AppNav.vue'
import ToastContainer from '@/components/ToastContainer.vue'

const route = useRoute()
const navPadding = computed(() => route.name === 'history' ? 'pt-32' : 'pt-20')
</script>

<template>
  <div class="min-h-screen flex flex-col bg-slate-50 dark:bg-slate-900">
    <AppNav />
    <main :class="[navPadding, 'flex-1 max-w-7xl w-full mx-auto px-6 pb-8']">
      <RouterView />
    </main>
    <ToastContainer />
  </div>
</template>
