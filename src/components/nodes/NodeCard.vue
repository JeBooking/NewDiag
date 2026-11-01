<script setup lang="ts">
import { computed } from 'vue'
import { Handle, Position } from '@vue-flow/core'
import { KIND_META } from '@/domain/kinds'
import type { NodeCardData } from './index'

// 画布会把 position/dimensions 等内部字段一并透传，避免它们落到 DOM 属性上
defineOptions({ inheritAttrs: false })

const props = withDefaults(
  defineProps<{
    data: NodeCardData
    selected?: boolean
  }>(),
  { selected: false },
)

const meta = computed(() => KIND_META[props.data.kind])
const isSelected = computed(() => props.selected || props.data.selected)
const isStart = computed(() => props.data.kind === 'start')
const badge = computed(() => (props.data.hasBinding ? '自动' : '人工'))
const statusClass = computed(() => (props.data.status ? `status-${props.data.status}` : ''))
const issues = computed(() => props.data.issues ?? [])
/** start 卡片副标题换成启动参数摘要，其余节点仍显示说明 */
const subtitle = computed(() => {
  if (isStart.value) {
    const count = props.data.paramCount ?? 0
    return count > 0 ? `${count} 个启动参数` : '未声明参数'
  }
  return props.data.detail || '暂无说明'
})
const worst = computed(() => {
  if (issues.value.some((issue) => issue.level === 'error')) return 'error'
  return issues.value.length > 0 ? 'warning' : ''
})
</script>

<template>
  <div
    class="node-card"
    :class="[statusClass, { 'is-selected': isSelected }]"
    :style="{ '--kind-color': meta.color }"
  >
    <span class="stripe" />

    <Handle
      id="in"
      type="target"
      :position="Position.Left"
      class="h-in"
      :class="{ 'is-hidden': !data.hasTarget }"
    />

    <div class="content">
      <div class="row-head">
        <span class="kind">{{ meta.label }}</span>
        <span class="dot" />
      </div>
      <div class="title">{{ data.title || '未命名节点' }}</div>
      <div class="detail" :class="{ 'is-param': isStart }">
        {{ subtitle }}
      </div>
      <div v-if="!isStart" class="row-foot">
        <span class="badge" :class="{ 'is-auto': data.hasBinding }">{{ badge }}</span>
      </div>
    </div>

    <div v-if="issues.length" class="issue-wrap" :class="worst">
      <span class="issue-badge">{{ issues.length > 9 ? '9+' : issues.length }}</span>
      <div class="issue-pop">
        <div class="issue-pop-head">
          {{ worst === 'error' ? '校验错误' : '提示' }} · {{ issues.length }} 条
        </div>
        <div v-for="(issue, index) in issues" :key="index" class="issue-row">
          <span class="issue-dot" :class="issue.level" />
          <span class="issue-text">{{ issue.message }}</span>
        </div>
      </div>
    </div>

    <template v-if="meta.branch === 'yesno'">
      <Handle id="yes" type="source" :position="Position.Right" class="h-out h-yes" />
      <Handle id="no" type="source" :position="Position.Right" class="h-out h-no" />
      <span class="branch-label branch-yes">是</span>
      <span class="branch-label branch-no">否</span>
    </template>
    <Handle
      v-else-if="meta.branch === 'single'"
      type="source"
      :position="Position.Right"
      class="h-out"
    />
  </div>
</template>

<style scoped>
.node-card {
  position: relative;
  width: 220px;
  min-height: 96px;
  padding: 8px 12px 8px 16px;
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  background: var(--el-bg-color-overlay);
  box-shadow: 0 1px 2px rgb(15 23 42 / 8%);
  font-size: 12px;
  line-height: 1.4;
  transition: border-color 0.15s, box-shadow 0.15s;
}

.stripe {
  position: absolute;
  top: 0;
  left: 0;
  bottom: 0;
  width: 4px;
  border-radius: 6px 0 0 6px;
  background: var(--kind-color);
}

.content {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.row-head {
  display: flex;
  align-items: center;
  gap: 6px;
}

.kind {
  padding: 0 5px;
  border-radius: 3px;
  font-size: 11px;
  color: var(--kind-color);
  background: color-mix(in srgb, var(--kind-color) 12%, transparent);
}

.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--el-text-color-placeholder);
}

.title {
  overflow: hidden;
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.detail {
  display: -webkit-box;
  overflow: hidden;
  color: var(--el-text-color-secondary);
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

/* 开始节点的副标题是参数摘要，不会换行 */
.detail.is-param {
  display: block;
  white-space: nowrap;
  text-overflow: ellipsis;
  -webkit-line-clamp: 1;
}

.row-foot {
  display: flex;
  justify-content: flex-end;
}

.badge {
  padding: 0 4px;
  border: 1px solid var(--el-border-color);
  border-radius: 3px;
  font-size: 10px;
  color: var(--el-text-color-secondary);
}

.badge.is-auto {
  border-color: color-mix(in srgb, var(--kind-color) 40%, transparent);
  color: var(--kind-color);
}

.is-selected {
  border-color: var(--el-color-primary);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--el-color-primary) 25%, transparent);
}

.issue-wrap {
  position: absolute;
  top: -8px;
  right: -8px;
  z-index: 3;
}

.issue-badge {
  display: block;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  border: 1px solid var(--el-bg-color-overlay);
  border-radius: 8px;
  font-size: 10px;
  line-height: 14px;
  color: #fff;
  text-align: center;
  background: var(--el-color-warning);
  cursor: default;
}

.issue-wrap.error .issue-badge {
  background: var(--el-color-danger);
}

.issue-pop {
  position: absolute;
  top: 18px;
  right: 0;
  display: none;
  width: 240px;
  padding: 8px 10px;
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  background: var(--el-bg-color-overlay);
  box-shadow: 0 6px 18px rgb(15 23 42 / 16%);
}

.issue-wrap:hover .issue-pop {
  display: block;
}

.issue-pop-head {
  margin-bottom: 6px;
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.issue-row {
  display: flex;
  gap: 6px;
  align-items: flex-start;
  padding: 2px 0;
  line-height: 1.5;
}

.issue-dot {
  flex: none;
  width: 6px;
  height: 6px;
  margin-top: 5px;
  border-radius: 50%;
  background: var(--el-color-warning);
}

.issue-dot.error {
  background: var(--el-color-danger);
}

.issue-text {
  color: var(--el-text-color-primary);
}

.h-in {
  width: 9px;
  height: 9px;
  border: 1px solid #fff;
  background: var(--el-text-color-placeholder);
}

.h-in.is-hidden {
  opacity: 0;
  pointer-events: none;
}

.h-out {
  width: 9px;
  height: 9px;
  border: 1px solid #fff;
  background: var(--kind-color);
}

.h-yes {
  top: 36%;
}

.h-no {
  top: 68%;
}

.branch-label {
  position: absolute;
  right: 10px;
  font-size: 10px;
  pointer-events: none;
}

.branch-yes {
  top: 26%;
  color: #16a34a;
}

.branch-no {
  top: 58%;
  color: #dc2626;
}

.status-running {
  border-color: #2563eb;
  box-shadow: 0 0 0 2px rgb(37 99 235 / 22%);
}

.status-running .dot {
  background: #2563eb;
  animation: pulse 1.2s infinite;
}

.status-waiting {
  border-color: #d97706;
}

.status-waiting .dot {
  background: #d97706;
}

.status-success {
  border-color: #16a34a;
}

.status-success .dot {
  background: #16a34a;
}

.status-failed {
  border-color: #dc2626;
}

.status-failed .dot {
  background: #dc2626;
}

@keyframes pulse {
  50% {
    opacity: 0.25;
  }
}
</style>
