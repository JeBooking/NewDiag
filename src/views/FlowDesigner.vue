<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { FullScreen, RefreshLeft, RefreshRight, Share, ZoomIn, ZoomOut } from '@element-plus/icons-vue'
import AppHeader from '@/components/layout/AppHeader.vue'
import FlowCanvas from '@/components/canvas/FlowCanvas.vue'
import { layoutDoc } from '@/components/canvas/autolayout'
import FlowPanel from '@/components/panels/FlowPanel.vue'
import NodePanel from '@/components/panels/NodePanel.vue'
import { useLint } from '@/components/canvas/lint'
import type { DocNode, FlowDoc, FlowMode, LintIssue } from '@/domain/types'
import { errorText, isApiError } from '@/services/api'
import { updateFlow } from '@/services/flows'
import { useEditorStore } from '@/stores/editor'

const route = useRoute()
const router = useRouter()
const editor = useEditorStore()

const loadError = ref<string | null>(null)
const issuesVisible = ref(false)
const canvas = ref<InstanceType<typeof FlowCanvas> | null>(null)

const flowId = computed(() => String(route.params.id))

/** 校验器要求一份文档，加载完成前给个空壳，避免 useLint 首次执行时报「流程为空」 */
const EMPTY_DOC: FlowDoc = { format: 1, name: '', mode: 'parallel', nodes: [], edges: [] }
const lintInput = computed<FlowDoc>(() => editor.doc ?? EMPTY_DOC)
const liveIssues = useLint(() => lintInput.value)

/**
 * 编辑期靠本地校验即时反馈，保存/发布失败时后端还会回一份更严格的结果。
 * 后端返回的条目没有 id，只能按「级别 + 节点 + 连线 + 文案」合成键去重，
 * 否则一张 Map 会把所有后端问题压成一条。
 */
function issueKey(issue: LintIssue): string {
  return (
    issue.id ?? `${issue.level}|${issue.nodeId ?? ''}|${issue.edgeId ?? ''}|${issue.message}`
  )
}

const allIssues = computed<LintIssue[]>(() => {
  if (!editor.doc) return []
  const merged = new Map<string, LintIssue>()
  for (const issue of liveIssues.value) merged.set(issueKey(issue), issue)
  for (const issue of editor.issues) merged.set(issueKey(issue), issue)
  return [...merged.values()]
})

const errorCount = computed(() => allIssues.value.filter((item) => item.level === 'error').length)

async function load(id: string): Promise<void> {
  loadError.value = null
  try {
    await editor.open(id)
  } catch (error) {
    loadError.value = errorText(error, '加载流程失败')
  }
}

onMounted(() => {
  void load(flowId.value)
})

watch(flowId, (id) => {
  if (id && id !== editor.flow?.id) void load(id)
})

function onDocUpdate(next: FlowDoc): void {
  editor.applyDoc(next)
}

function onSelect(nodeId: string | null): void {
  editor.selectedNodeId = nodeId
}

/* ---------- 画布基础操作 ---------- */

/** 缩放读数与按钮都落在画布组件里，这里只做转发 */
const zoomPercent = computed(() => canvas.value?.zoomPercent ?? 100)

function zoomInCanvas(): void {
  void canvas.value?.zoomIn()
}

function zoomOutCanvas(): void {
  void canvas.value?.zoomOut()
}

function resetZoom(): void {
  canvas.value?.resetZoom()
}

function fitCanvas(): void {
  void canvas.value?.fitView()
}

/** 焦点在输入框时把撤销让给浏览器，否则打字会把整份文档退回去 */
function isTyping(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null
  if (!element) return false
  if (element.isContentEditable) return true
  return ['INPUT', 'TEXTAREA', 'SELECT'].includes(element.tagName)
}

function onKeydown(event: KeyboardEvent): void {
  if (!(event.ctrlKey || event.metaKey) || event.altKey || isTyping(event.target)) return
  const key = event.key.toLowerCase()
  if (key === 'z') {
    event.preventDefault()
    if (event.shiftKey) editor.redo()
    else editor.undo()
  } else if (key === 'y') {
    event.preventDefault()
    editor.redo()
  }
}

onMounted(() => window.addEventListener('keydown', onKeydown))

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  editor.close()
})

/** 整图重排：坐标覆盖式写入，dirty 与取锁都交给 applyDoc */
async function autoLayout(): Promise<void> {
  const doc = editor.doc
  if (!doc) return
  editor.applyDoc(layoutDoc(doc))
  // 投影在 watch 里重建，等画布按新坐标渲染完这一帧再取景，否则取到的还是旧范围
  await nextTick()
  await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()))
  await canvas.value?.fitView()
}

function issueNodeName(issue: LintIssue): string {
  if (!issue.nodeId) return ''
  const node = editor.doc?.nodes.find((item) => item.id === issue.nodeId)
  return node?.title || issue.nodeId
}

function locateIssue(issue: LintIssue): void {
  if (issue.nodeId) editor.selectedNodeId = issue.nodeId
  issuesVisible.value = false
}

/** 选中节点的出边按分支分组：是/否/默认各自排序，互不干扰 */
const successorGroups = computed(() => {
  const node = editor.selectedNode
  const doc = editor.doc
  if (!node || !doc) return []
  const groups = new Map<string, { edgeId: string; title: string; order: number }[]>()
  for (const edge of doc.edges) {
    if (edge.from !== node.id) continue
    const key = edge.branch === 'yes' || edge.branch === 'no' ? edge.branch : 'default'
    const list = groups.get(key) ?? []
    list.push({
      edgeId: edge.id,
      title: doc.nodes.find((item) => item.id === edge.to)?.title || edge.to,
      order: edge.order ?? 1,
    })
    groups.set(key, list)
  }
  const rank = ['yes', 'no', 'default']
  return [...groups.entries()]
    .sort((a, b) => rank.indexOf(a[0]) - rank.indexOf(b[0]))
    .map(([branch, list]) => ({
      branch,
      label: branch === 'yes' ? '是分支' : branch === 'no' ? '否分支' : '后续节点',
      items: list
        .sort((a, b) => a.order - b.order)
        .map(({ edgeId, title }) => ({ edgeId, title })),
    }))
})

function reorderEdge(edgeId: string, delta: number): void {
  const doc = editor.doc
  if (!doc) return
  // 只在同一条分支组内换位，别的组顺序不动
  const group = successorGroups.value.find((item) =>
    item.items.some((edge) => edge.edgeId === edgeId),
  )
  if (!group) return
  const list = group.items.slice()
  const index = list.findIndex((item) => item.edgeId === edgeId)
  const target = index + delta
  if (index < 0 || target < 0 || target >= list.length) return
  const [moved] = list.splice(index, 1)
  list.splice(target, 0, moved)
  const orderById = new Map(list.map((item, i) => [item.edgeId, i + 1]))
  editor.applyDoc({
    ...doc,
    edges: doc.edges.map((edge) =>
      orderById.has(edge.id) ? { ...edge, order: orderById.get(edge.id) } : edge,
    ),
  })
}

function patchNode(patch: Partial<DocNode>): void {
  const doc = editor.doc
  const node = editor.selectedNode
  if (!doc || !node) return
  editor.applyDoc({
    ...doc,
    nodes: doc.nodes.map((item) => (item.id === node.id ? { ...item, ...patch } : item)),
  })
}

function deleteNode(): void {
  const doc = editor.doc
  const node = editor.selectedNode
  if (!doc || !node) return
  if (node.kind === 'start') {
    ElMessage.warning('开始节点是流程入口，不能删除')
    return
  }
  editor.applyDoc({
    ...doc,
    nodes: doc.nodes.filter((item) => item.id !== node.id),
    edges: doc.edges.filter((edge) => edge.from !== node.id && edge.to !== node.id),
  })
  editor.selectedNodeId = null
}

/** 执行只认发布版本，没有版本时给出明确入口而不是让用户点进空页面 */
function startRun(): void {
  if ((editor.flow?.releases?.length ?? 0) === 0) {
    ElMessage.warning('还没有发布版本，先发布再执行')
    return
  }
  router.push({ name: 'flow-run', params: { id: flowId.value } })
}

function patchSnapshotFields(fields: string[]): void {
  const doc = editor.doc
  if (!doc) return
  editor.applyDoc({ ...doc, snapshotFields: fields })
}

async function patchMeta(patch: { name?: string; mode?: FlowMode }): Promise<void> {  if (!editor.flow) return
  try {
    const updated = await updateFlow(editor.flow.id, patch)
    editor.patchFlowMeta({
      name: updated.name,
      description: updated.description,
      mode: updated.mode,
      updatedAt: updated.updatedAt,
    })
    ElMessage.success('已更新流程属性')
  } catch (error) {
    ElMessage.error(errorText(error, '更新失败'))
  }
}

function showFailure(error: unknown, fallback: string): void {
  if (isApiError(error) && error.issues.length > 0) {
    issuesVisible.value = true
    return
  }
  ElMessage.error(errorText(error, fallback))
}

async function save(silent = false): Promise<boolean> {
  try {
    await editor.save()
    if (!silent) ElMessage.success('已保存')
    return true
  } catch (error) {
    showFailure(error, '保存失败')
    return false
  }
}

async function publish(): Promise<void> {
  if (editor.dirty) {
    try {
      await ElMessageBox.confirm(
        '发布基于已保存的在线版本，当前改动尚未提交。是否先保存再发布？',
        '发布前保存',
        { type: 'warning', confirmButtonText: '保存并发布', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
    const ok = await save(true)
    if (!ok) return
  }
  try {
    const result = await editor.publish()
    ElMessage.success(`已发布 v${result.versionNo}`)
    router.push({ name: 'flow-releases', params: { id: flowId.value } })
  } catch (error) {
    showFailure(error, '发布失败')
  }
}

async function discard(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      '放弃后本次检出产生的所有改动都会丢弃，编辑锁随即释放。确定放弃？',
      '放弃编辑',
      { type: 'warning', confirmButtonText: '放弃改动', cancelButtonText: '继续编辑' },
    )
  } catch {
    return
  }
  try {
    await editor.discard()
    ElMessage.success('已放弃改动')
    router.push({ name: 'flows' })
  } catch (error) {
    ElMessage.error(errorText(error, '放弃失败'))
  }
}

function backToList(): void {
  router.push({ name: 'flows' })
}

onBeforeRouteLeave(async () => {
  if (!editor.dirty) return true
  try {
    await ElMessageBox.confirm(
      '当前改动已暂存为草稿但尚未提交，离开后可重新进入继续编辑。确定离开？',
      '离开设计页',
      { type: 'warning', confirmButtonText: '离开', cancelButtonText: '留下' },
    )
    return true
  } catch {
    return false
  }
})
</script>

<template>
  <div class="designer">
    <AppHeader />

    <div class="toolbar">
      <div class="left">
        <el-button text @click="backToList">← 返回</el-button>
        <span class="flow-name">{{ editor.flow?.name || '加载中' }}</span>
        <el-tag v-if="editor.dirty" size="small" type="warning" effect="plain">未提交</el-tag>
        <el-tag v-else-if="editor.flow" size="small" type="info" effect="plain">已同步</el-tag>
      </div>
      <div class="right">
        <button
          v-if="allIssues.length"
          type="button"
          class="issue-chip"
          :class="{ 'is-error': errorCount > 0 }"
          @click="issuesVisible = true"
        >
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="M5 5L11 11M11 5L5 11" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
          </svg>
          {{ allIssues.length }} 处待处理
        </button>
        <el-button @click="startRun">开始执行</el-button>
        <el-button :disabled="editor.readonly" :loading="editor.saving" @click="save()">
          保存
        </el-button>
        <el-button type="primary" :disabled="editor.readonly" :loading="editor.publishing" @click="publish">
          发布
        </el-button>
        <el-button type="danger" plain :disabled="editor.readonly" @click="discard">放弃</el-button>
      </div>
    </div>

    <el-alert
      v-if="editor.readonly && editor.flow"
      :title="`${editor.flow.editingBy || '其他用户'} 正在编辑，当前页面只读`"
      description="请等待对方保存或放弃后重新进入"
      type="warning"
      :closable="false"
      show-icon
      class="banner"
    />

    <el-alert
      v-if="loadError"
      :title="loadError"
      type="error"
      :closable="false"
      show-icon
      class="banner"
    />

    <!-- 画布基础操作：撤销重做只动文档，与保存/发布这类流程级动作分开 -->
    <div class="opsbar">
      <el-button-group>
        <el-tooltip content="撤销 (Ctrl+Z)" placement="bottom">
          <el-button
            :icon="RefreshLeft"
            :disabled="!editor.editable || !editor.canUndo"
            @click="editor.undo()"
          />
        </el-tooltip>
        <el-tooltip content="重做 (Ctrl+Shift+Z)" placement="bottom">
          <el-button
            :icon="RefreshRight"
            :disabled="!editor.editable || !editor.canRedo"
            @click="editor.redo()"
          />
        </el-tooltip>
      </el-button-group>

      <span class="opsbar-divider" />

      <el-button-group>
        <el-tooltip content="缩小" placement="bottom">
          <el-button
            :icon="ZoomOut"
            :disabled="!canvas || zoomPercent <= 20"
            @click="zoomOutCanvas"
          />
        </el-tooltip>
        <el-tooltip content="恢复 100%" placement="bottom">
          <el-button class="zoom-readout" :disabled="!canvas" @click="resetZoom">
            {{ zoomPercent }}%
          </el-button>
        </el-tooltip>
        <el-tooltip content="放大" placement="bottom">
          <el-button :icon="ZoomIn" :disabled="!canvas || zoomPercent >= 200" @click="zoomInCanvas" />
        </el-tooltip>
      </el-button-group>

      <el-button :icon="FullScreen" :disabled="!canvas" @click="fitCanvas">适应画布</el-button>
      <el-button :icon="Share" :disabled="!editor.editable" @click="autoLayout">自动布局</el-button>
    </div>

    <div class="body" v-loading="editor.loading">
      <div class="canvas-area">
        <FlowCanvas
          v-if="editor.doc"
          ref="canvas"
          :doc="editor.doc"
          :readonly="editor.readonly"
          :issues="editor.issues"
          :status="{}"
          :selected-node-id="editor.selectedNodeId"
          @update:doc="onDocUpdate"
          @select-node="onSelect"
        />
        <el-empty v-else-if="!editor.loading" description="流程不存在或没有可编辑的文档" />
      </div>

      <aside class="panel">
        <NodePanel
          v-if="editor.selectedNode"
          :node="editor.selectedNode"
          :successor-groups="successorGroups"
          :mode="editor.doc?.mode ?? 'parallel'"
          :readonly="editor.readonly"
          :flow-id="editor.flow?.id"
          :doc="editor.doc ?? undefined"
          @update="patchNode"
          @delete="deleteNode"
          @reorder="reorderEdge"
        />
        <FlowPanel
          v-else-if="editor.flow"
          :flow="editor.flow"
          :snapshot-fields="editor.doc?.snapshotFields ?? []"
          :readonly="editor.readonly"
          :publishing="editor.publishing"
          @update-meta="patchMeta"
          @update-snapshot-fields="patchSnapshotFields"
          @publish="publish"
        />
      </aside>
    </div>

    <el-dialog v-model="issuesVisible" title="校验问题" width="560px">
      <div class="issue-list">
        <button
          v-for="issue in allIssues"
          :key="issueKey(issue)"
          type="button"
          class="issue"
          @click="locateIssue(issue)"
        >
          <el-tag size="small" :type="issue.level === 'warning' ? 'warning' : 'danger'" effect="plain">
            {{ issue.level === 'warning' ? '提示' : '错误' }}
          </el-tag>
          <span class="issue-text">{{ issue.message }}</span>
          <span v-if="issueNodeName(issue)" class="issue-node">{{ issueNodeName(issue) }}</span>
        </button>
        <div v-if="!allIssues.length" class="issue-empty">没有发现问题</div>
      </div>
      <template #footer>
        <el-button type="primary" @click="issuesVisible = false">知道了</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.designer {
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow: hidden;
}

.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 8px 16px;
  background: var(--el-bg-color);
  border-bottom: 1px solid var(--el-border-color-light);
  flex: none;
}

.left,
.right {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* 第二条栏只放画布级动作，视觉上比顶部流程栏低一档 */
.opsbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 16px;
  background: var(--el-bg-color);
  border-bottom: 1px solid var(--el-border-color-light);
  flex: none;
}

.opsbar :deep(.el-button-group) {
  display: inline-flex;
}

.opsbar-divider {
  width: 1px;
  height: 18px;
  background: var(--el-border-color);
}

.zoom-readout {
  min-width: 58px;
  font-variant-numeric: tabular-nums;
}

.flow-name {
  font-size: 14px;
  font-weight: 600;
}

.banner {
  flex: none;
  border-radius: 0;
}

/* 常驻的问题计数：校验结果一直可见，不必先点保存才知道有错 */
.issue-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 5px 10px;
  border: 1px solid var(--el-color-warning-light-5);
  border-radius: 6px;
  background: var(--el-color-warning-light-9);
  font: inherit;
  font-size: 12px;
  color: var(--el-color-warning-dark-2);
  cursor: pointer;
}

.issue-chip:hover {
  border-color: var(--el-color-warning);
}

.issue-chip.is-error {
  border-color: var(--el-color-danger-light-5);
  color: var(--el-color-danger);
  background: var(--el-color-danger-light-9);
}

.issue-chip.is-error:hover {
  border-color: var(--el-color-danger);
}

.issue-chip svg {
  width: 12px;
  height: 12px;
}

.body {
  flex: 1;
  display: flex;
  min-height: 0;
}

.canvas-area {
  flex: 1;
  min-width: 0;
  position: relative;
  background: var(--el-bg-color-page);
}

.panel {
  width: var(--app-panel-width);
  flex: none;
  border-left: 1px solid var(--el-border-color-light);
  background: var(--el-bg-color);
  padding: 14px 16px 24px;
  overflow: auto;
}

.issue-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 360px;
  overflow: auto;
}

.issue {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 4px 0;
  border: none;
  background: none;
  font: inherit;
  font-size: 12px;
  line-height: 1.6;
  text-align: left;
  cursor: pointer;
}

.issue:hover {
  color: var(--el-color-primary);
}

.issue-empty {
  padding: 12px 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.issue-text {
  flex: 1;
}

.issue-node {
  color: var(--el-text-color-secondary);
  font-family: ui-monospace, Consolas, monospace;
  font-size: 11px;
}
</style>
