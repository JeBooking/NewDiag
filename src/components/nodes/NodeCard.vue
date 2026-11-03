<script setup lang="ts">
import { computed } from 'vue'
import { Handle, Position } from '@vue-flow/core'
import { KIND_META } from '@/domain/kinds'
import type { NodeVisualStatus } from '@/domain/types'
import { ICON_ERROR, ICON_WARNING } from '@/components/canvas/issue-icons'
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

/** 执行状态的唯一取色表：浅底 + 深字 + 同色点，与全局状态标签规范一致 */
const STATUS_META: Record<NodeVisualStatus, { text: string; color: string; bg: string }> = {
  running: { text: '运行中', color: '#0958D9', bg: '#E6F4FF' },
  waiting: { text: '待处理', color: '#D48806', bg: '#FFFBE6' },
  success: { text: '已完成', color: '#389E0D', bg: '#F6FFED' },
  failed: { text: '失败', color: '#CF1322', bg: '#FFF1F0' },
  queued: { text: '排队中', color: '#595959', bg: '#F5F5F5' },
  pending: { text: '已回退', color: '#8C8C8C', bg: '#F5F5F5' },
}

const meta = computed(() => KIND_META[props.data.kind])
const isSelected = computed(() => props.selected || props.data.selected)
const isStart = computed(() => props.data.kind === 'start')
const badge = computed(() => props.data.executionLabel ?? (props.data.hasBinding ? '自动' : '人工'))
const flowClass = computed(() => (props.data.flow ? `flow-${props.data.flow}` : ''))
const issues = computed(() => props.data.issues ?? [])
const runCount = computed(() => props.data.runCount ?? 0)
const reviewCount = computed(() => props.data.reviewCount ?? 0)
const status = computed(() => (props.data.status ? STATUS_META[props.data.status] : null))

/** 待办优先于历史记录：有待办时节点上只留「处理 N」这一个动作，点了才进得去处理 */
const action = computed<{ text: string; tone: 'todo' | 'history' } | null>(() => {
  if (reviewCount.value > 0) return { text: `处理 ${reviewCount.value}`, tone: 'todo' }
  if (runCount.value > 0) return { text: `记录 ${runCount.value}`, tone: 'history' }
  return null
})

/** 待处理 / 运行中再用左侧色带做第二重提示，避免只靠右上角一个小标签 */
const accent = computed(() =>
  props.data.status === 'waiting' || props.data.status === 'running' ? props.data.status : null,
)

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
    :class="[
      flowClass,
      accent ? `accent-${accent}` : '',
      `trail-${data.trailState}`,
      {
        'is-selected': isSelected,
        'is-running': data.status === 'running',
      },
    ]"
    :style="{
      '--kind-color': meta.color,
      '--status-color': status?.color ?? 'transparent',
      '--status-bg': status?.bg ?? 'transparent',
    }"
  >
    <span class="stripe" />
    <span v-if="status" class="status-bar" :class="`bar-${data.status}`" />

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
        <span v-if="status" class="status-tag">
          <span class="status-dot" />
          {{ status.text }}
        </span>
      </div>
      <div class="title">{{ data.title || '未命名节点' }}</div>
      <div class="detail" :class="{ 'is-param': isStart }">
        {{ subtitle }}
      </div>
      <div v-if="!isStart || action" class="row-foot">
        <span v-if="!isStart" class="badge" :class="{ 'is-auto': data.hasBinding }">{{ badge }}</span>
        <!-- 不要加 .stop：点击要靠冒泡到 vue-flow 的 node-click，
             由 FlowCanvas.onNodeClick 用 closest('.node-action') 区分「看详情」和「选中」 -->
        <button
          v-if="action"
          type="button"
          class="node-action"
          :class="`tone-${action.tone}`"
        >
          {{ action.text }}
        </button>
      </div>
    </div>

    <div v-if="issues.length" class="issue-wrap" :class="worst">
      <span class="issue-badge">
        <svg v-if="worst === 'error'" viewBox="0 0 512 512" aria-hidden="true">
          <path :d="ICON_ERROR" fill="currentColor" />
        </svg>
        <svg v-else viewBox="0 0 512 512" aria-hidden="true">
          <path :d="ICON_WARNING" fill="currentColor" />
        </svg>
      </span>
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
  padding: 12px 12px 10px 16px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-bg-color-overlay);
  box-shadow: 0 1px 2px rgb(0 0 0 / 3%);
  font-size: 12px;
  line-height: 1.4;
  transition:
    opacity 0.2s,
    border-color 0.15s,
    box-shadow 0.15s;
}

.stripe {
  position: absolute;
  top: 0;
  left: 0;
  bottom: 0;
  width: 4px;
  border-radius: 8px 0 0 8px;
  background: var(--kind-color);
}

/* 状态条只占顶部 3px，把「1px 边框」这个通道整个让给选中态，两种语义不再互相覆盖 */
.status-bar {
  position: absolute;
  top: 0;
  left: 4px;
  right: 0;
  height: 3px;
  overflow: hidden;
  border-radius: 0 8px 0 0;
  background: color-mix(in srgb, var(--status-color) 26%, transparent);
}

.bar-running {
  background: #1677ff;
}

.bar-running::after {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  width: 42%;
  background: rgb(255 255 255 / 70%);
  animation: run-sweep 1.5s ease-in-out infinite;
}

.content {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.row-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
}

.kind {
  padding: 0 5px;
  border-radius: 2px;
  font-size: 11px;
  color: var(--kind-color);
  background: color-mix(in srgb, var(--kind-color) 12%, transparent);
}

.status-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex: none;
  padding: 0 5px;
  border-radius: 2px;
  font-size: 11px;
  line-height: 16px;
  color: var(--status-color);
  background: var(--status-bg);
}

.status-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentcolor;
}

.is-running .status-dot {
  animation: pulse 1.2s ease-in-out infinite;
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
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  min-height: 16px;
}

.badge {
  padding: 0 4px;
  border: 1px solid var(--el-border-color);
  border-radius: 2px;
  font-size: 10px;
  color: var(--el-text-color-secondary);
}

.badge.is-auto {
  border-color: color-mix(in srgb, var(--kind-color) 40%, transparent);
  color: var(--kind-color);
}

/* 动作入口：有待办时是实心警示按钮，只有历史记录时降为文字链，不和主操作抢注意力 */
.node-action {
  flex: none;
  padding: 1px 8px;
  border: 1px solid transparent;
  border-radius: 4px;
  font: inherit;
  font-size: 11px;
  line-height: 16px;
  cursor: pointer;
}

.node-action.tone-todo {
  color: #fff;
  background: #faad14;
}

.node-action.tone-todo:hover {
  background: #d48806;
}

.node-action.tone-history {
  padding: 1px 4px;
  color: var(--el-color-primary);
  background: none;
}

.node-action.tone-history:hover {
  text-decoration: underline;
}

/*
 * 三档路径状态，和状态色正交：
 * 当前路径不动；走过但作废的用「虚线 + 灰底 + 灰字」降级，状态标签保留它当时的结果；
 * 从未到访的更淡、并且不显示任何执行状态。
 * 不靠父级 opacity 压暗，否则状态标签的颜色会被一起吃掉，失败和完成就分不出来了。
 */
.trail-dropped {
  border-style: dashed;
  border-color: #b8bec8;
  background-color: #f7f8fa;
  background-image: repeating-linear-gradient(
    -45deg,
    rgb(0 0 0 / 2.5%) 0 6px,
    transparent 6px 12px
  );
}

.trail-dropped .title {
  color: var(--el-text-color-secondary);
}

.trail-dropped .detail {
  color: var(--el-text-color-placeholder);
}

.trail-dropped .kind,
.trail-dropped .badge {
  opacity: 0.55;
}

.trail-dropped .stripe {
  opacity: 0.45;
}

/* 空心标签：颜色保留（还能分清红/绿），但不再抢注意力 */
.trail-dropped .status-tag {
  background: transparent;
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--status-color) 38%, transparent);
  opacity: 0.85;
}

.trail-unvisited {
  border-color: #e4e7ed;
  background: #fafbfc;
}

.trail-unvisited .title {
  font-weight: 500;
  color: var(--el-text-color-placeholder);
}

.trail-unvisited .detail {
  color: var(--el-text-color-placeholder);
  opacity: 0.7;
}

.trail-unvisited .kind,
.trail-unvisited .badge {
  opacity: 0.4;
}

.trail-unvisited .stripe {
  opacity: 0.22;
}

/*
 * 正在跑：常驻实色外圈 + 向外扩散的脉冲。
 * 刻意不复用 box-shadow（选中态在用）和顶部状态条（排队/等待也会用）。
 * 外圈用半实色而不是 12% 那种透明圈：系统开启「减少动画」时扩散会被关掉，
 * 静态外圈必须自己站得住，所以它比动效更重要。
 */
.is-running::before {
  content: '';
  position: absolute;
  inset: -5px;
  border-radius: 13px;
  border: 2px solid #1677ff;
  box-shadow: 0 0 0 4px rgb(22 119 255 / 14%);
  pointer-events: none;
}

.is-running::after {
  content: '';
  position: absolute;
  inset: -4px;
  border-radius: 12px;
  border: 2px solid #1677ff;
  opacity: 0;
  pointer-events: none;
  animation: run-pulse 1.4s ease-out infinite;
}

/*
 * 等人工处理才是诊断流程里最常见的「当前一步」，只给 running 做特效等于看不到。
 * 同一套外圈结构、换成警示色，动效改成慢呼吸而不是向外扩散，两者一眼能分开。
 */
.accent-waiting::before {
  content: '';
  position: absolute;
  inset: -5px;
  border-radius: 13px;
  border: 2px solid #faad14;
  box-shadow: 0 0 0 4px rgb(250 173 20 / 14%);
  pointer-events: none;
}

.accent-waiting::after {
  content: '';
  position: absolute;
  inset: -5px;
  border-radius: 13px;
  border: 2px solid rgb(250 173 20 / 70%);
  pointer-events: none;
  animation: wait-breathe 2.4s ease-in-out infinite;
}

/* 待处理 / 运行中用左侧色带覆盖类型色：这一屏里「它在等我」比「它是什么类型」更重要 */
.accent-waiting .stripe {
  background: #faad14;
}

.accent-running .stripe {
  background: #1677ff;
}

.flow-upstream {
  border-color: #2563eb;
  box-shadow: 0 0 0 2px rgb(37 99 235 / 18%);
}

.flow-downstream {
  border-color: #16a34a;
  box-shadow: 0 0 0 2px rgb(22 163 74 / 18%);
}

.is-selected {
  border-color: var(--el-color-primary);
  box-shadow: 0 0 0 1px var(--el-color-primary);
}

.issue-wrap {
  position: absolute;
  top: -7px;
  right: -7px;
  z-index: 3;
}

/* 与旧项目一致：20px 圆形角标、内边距 5px、白图标，错误红底、提示黄底，不显示条数 */
.issue-badge {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  padding: 5px;
  border-radius: 50%;
  color: #fff;
  background: #f7c71a;
}

.issue-wrap.error .issue-badge {
  background: #cc0000;
}

.issue-badge svg {
  width: 100%;
  height: 100%;
}

.issue-pop {
  position: absolute;
  top: 18px;
  right: 0;
  display: none;
  width: 240px;
  padding: 8px 10px;
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  background: var(--el-bg-color-overlay);
  box-shadow: 0 6px 16px rgb(0 0 0 / 8%);
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

@keyframes pulse {
  50% {
    opacity: 0.25;
  }
}

@keyframes run-sweep {
  from {
    transform: translateX(-105%);
  }

  to {
    transform: translateX(240%);
  }
}

@keyframes run-pulse {
  0% {
    transform: scale(1);
    opacity: 0.9;
  }

  100% {
    transform: scale(1.12);
    opacity: 0;
  }
}

@keyframes wait-breathe {
  0%,
  100% {
    opacity: 0.15;
  }

  50% {
    opacity: 0.6;
  }
}

@media (prefers-reduced-motion: reduce) {
  .bar-running::after,
  .is-running .status-dot,
  .is-running::after,
  .accent-waiting::after {
    animation: none;
  }

  /* 减少动效时去掉扫光和脉冲环；实色外圈保留，静态也能看出这一张在跑 */
  .bar-running::after,
  .is-running::after,
  .accent-waiting::after {
    display: none;
  }
}
</style>
