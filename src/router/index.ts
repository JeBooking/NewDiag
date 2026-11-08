import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/figure', name: 'figure-viewer', component: () => import('@/views/FigureViewer.vue') },
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
    // 执行页是启动入口：填输入、点开始；历史执行去列表
    { path: '/flows/:id/run', name: 'flow-run', component: () => import('@/views/InstanceRun.vue') },
    { path: '/instances', name: 'instances', component: () => import('@/views/InstanceList.vue') },
    {
      path: '/instances/:id',
      name: 'instance',
      component: () => import('@/views/InstanceDetail.vue'),
    },
    { path: '/projects', name: 'projects', component: () => import('@/views/Projects.vue') },
    { path: '/trash', name: 'flow-trash', component: () => import('@/views/FlowTrash.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

// 动态导入失败多半是开发态模块图被 HMR 打断，或发版后旧 chunk 404；整页刷新一次即可恢复。
// 标记放 sessionStorage：模块真的坏了时只刷一次，不会来回刷新把页面卡死。
const RELOAD_FLAG = 'newdiag:chunk-reloaded'
router.onError((error) => {
  const message = String((error as Error)?.message || error)
  if (!/Failed to fetch dynamically imported module|Importing a module script failed/.test(message)) {
    return
  }
  if (sessionStorage.getItem(RELOAD_FLAG)) return
  sessionStorage.setItem(RELOAD_FLAG, '1')
  window.location.reload()
})

export default router
