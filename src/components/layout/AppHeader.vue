<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()

const active = computed(() => {
  if (route.path.startsWith('/instances')) return 'instances'
  if (route.path.startsWith('/projects')) return 'projects'
  return 'flows'
})
</script>

<template>
  <header class="app-header">
    <div class="brand">诊断流程平台</div>
    <nav class="nav">
      <router-link to="/" class="nav-item" :class="{ active: active === 'flows' }">流程</router-link>
      <router-link to="/instances" class="nav-item" :class="{ active: active === 'instances' }">
        执行记录
      </router-link>
      <router-link to="/projects" class="nav-item" :class="{ active: active === 'projects' }">
        工程
      </router-link>
    </nav>
    <div class="user">本地用户</div>
  </header>
</template>

<style scoped>
.app-header {
  display: flex;
  align-items: center;
  gap: 32px;
  height: var(--app-header-height);
  padding: 0 20px;
  background: var(--el-bg-color);
  border-bottom: 1px solid var(--el-border-color-light);
  flex: none;
}

.brand {
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0.5px;
}

.nav {
  display: flex;
  gap: 4px;
  flex: 1;
}

.nav-item {
  padding: 6px 12px;
  border-radius: 6px;
  color: var(--el-text-color-regular);
  font-size: 13px;
}

.nav-item:hover {
  background: var(--el-fill-color-light);
}

.nav-item.active {
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  font-weight: 500;
}

.user {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
</style>
