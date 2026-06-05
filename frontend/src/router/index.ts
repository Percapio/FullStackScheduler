import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import ReconciliationView    from '@/views/ReconciliationView.vue'
import ShippingView          from '@/views/ShippingView.vue'
import HistoryView           from '@/views/HistoryView.vue'
import BatchReviewView       from '@/views/BatchReviewView.vue'

const routes: RouteRecordRaw[] = [
  { path: '/',             redirect: '/reconciliation' },
  { path: '/reconciliation', name: 'reconciliation',
    component: ReconciliationView, meta: { label: 'Reconciliation' } },
  { path: '/shipping',     name: 'shipping',
    component: ShippingView,    meta: { label: 'Shipping' } },
  { path: '/history',      name: 'history',
    component: HistoryView,     meta: { label: 'History' } },
  { path: '/uploads/:batchId/review', name: 'batch-review',
    component: BatchReviewView },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})
