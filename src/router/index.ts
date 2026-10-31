import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'flows', component: () => import('@/views/FlowList.vue') },
  ],
})

export default router
