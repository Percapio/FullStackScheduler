import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
const routes: RouteRecordRaw[] = [
  { path: '/',               redirect: '/reconciliation' },
  { path: '/reconciliation', name: 'reconciliation',
    component: () => import('@/views/ReconciliationView.vue'), meta: { label: 'Reconciliation' } },
  { path: '/shipping',       name: 'shipping',
    component: () => import('@/views/ShippingView.vue'),       meta: { label: 'Shipping' } },
  { path: '/history',        name: 'history',
    component: () => import('@/views/HistoryView.vue'),        meta: { label: 'History' } },
  { path: '/uploads/:batchId/review', name: 'batch-review',
    component: () => import('@/views/BatchReviewView.vue') },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})
