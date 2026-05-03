import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import ReconciliationView from '@/views/ReconciliationView.vue'
import PlaceholderView    from '@/views/PlaceholderView.vue'
import ShippingView       from '@/views/ShippingView.vue'
import HistoryView        from '@/views/HistoryView.vue'

const routes: RouteRecordRaw[] = [
  { path: '/',             redirect: '/reconciliation' },
  { path: '/reconciliation', name: 'reconciliation',
    component: ReconciliationView, meta: { label: 'Reconciliation' } },
  { path: '/schedule',     name: 'schedule',
    component: PlaceholderView, meta: { label: 'Production Schedule', phase: 'TBD' } },
  { path: '/shipping',     name: 'shipping',
    component: ShippingView,    meta: { label: 'Shipping' } },
  { path: '/history',      name: 'history',
    component: HistoryView,     meta: { label: 'History' } },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})
