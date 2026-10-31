import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'flows', component: () => import('@/views/FlowList.vue') },
    {
      path: '/flows/:id/edit',
      name: 'flow-edit',
      component: () => import('@/views/FlowDesigner.vue'),
    },
    {
      path: '/flows/:id/releases',
      name: 'flow-releases',
      component: () => import('@/views/FlowReleases.vue'),
    },
    // 执行页按流程找到最新实例后跳转，本身不渲染画布
    { path: '/flows/:id/run', name: 'flow-run', component: () => import('@/views/InstanceRun.vue') },
    { path: '/instances', name: 'instances', component: () => import('@/views/InstanceList.vue') },
    {
      path: '/instances/:id',
      name: 'instance',
      component: () => import('@/views/InstanceDetail.vue'),
    },
    { path: '/projects', name: 'projects', component: () => import('@/views/Projects.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

export default router
