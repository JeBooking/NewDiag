<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import AppHeader from '@/components/layout/AppHeader.vue'
import FlowCanvas from '@/components/canvas/FlowCanvas.vue'
import FlowPanel from '@/components/panels/FlowPanel.vue'
import NodePanel from '@/components/panels/NodePanel.vue'
import type { DocNode, FlowDoc, FlowMode, LintIssue } from '@/domain/types'
import { errorText, isApiError } from '@/services/api'
import { updateFlow } from '@/services/flows'
import { useEditorStore } from '@/stores/editor'

const route = useRoute()
const router = useRouter()
const editor = useEditorStore()

const loadError = ref<string | null>(null)
const issuesVisible = ref(false)

const flowId = computed(() => String(route.params.id))

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

onBeforeUnmount(() => {
  editor.close()
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

function issueNodeName(issue: LintIssue): string {
  if (!issue.nodeId) return ''
  const node = editor.doc?.nodes.find((item) => item.id === issue.nodeId)
  return node?.title || issue.nodeId
}

function locateIssue(issue: LintIssue): void {
  if (issue.nodeId) editor.selectedNodeId = issue.nodeId
}

const saveHasError = computed(() =>
  editor.saveIssues.some((issue) => issue.level === 'error'),
)

/** 选中节点的出边，按 order 排好给属性面板调整 */
const successors = computed(() => {
  const node = editor.selectedNode
  const doc = editor.doc
  if (!node || !doc) return []
  return doc.edges
    .filter((edge) => edge.from === node.id)
    .slice()
    .sort((a, b) => (a.order ?? 1) - (b.order ?? 1))
    .map((edge) => ({
      edgeId: edge.id,
      title: doc.nodes.find((item) => item.id === edge.to)?.title || edge.to,
    }))
})

function reorderEdge(edgeId: string, delta: number): void {
  const doc = editor.doc
  if (!doc) return
  const list = successors.value.slice()
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
  editor.applyDoc({
    ...doc,
    nodes: doc.nodes.filter((item) => item.id !== node.id),
    edges: doc.edges.filter((edge) => edge.from !== node.id && edge.to !== node.id),
  })
  editor.selectedNodeId = null
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
      v-if="editor.saveIssues.length"
      :title="`已保存，${editor.saveIssues.length} 处待处理（发布前需解决）`"
      :type="saveHasError ? 'error' : 'warning'"
      :closable="true"
      show-icon
      class="banner"
      @close="editor.clearSaveIssues()"
    >
      <div class="warning-list">
        <button
          v-for="issue in editor.saveIssues"
          :key="issue.id"
          type="button"
          class="warning-item"
          @click="locateIssue(issue)"
        >
          <span class="issue-level" :class="issue.level">
            {{ issue.level === 'error' ? '错误' : '提示' }}
          </span>
          <span v-if="issueNodeName(issue)" class="warning-node">{{ issueNodeName(issue) }}</span>
          <span>{{ issue.message }}</span>
        </button>
      </div>
    </el-alert>

    <el-alert
      v-if="loadError"
      :title="loadError"
      type="error"
      :closable="false"
      show-icon
      class="banner"
    />

    <div class="body" v-loading="editor.loading">
      <div class="canvas-area">
        <FlowCanvas
          v-if="editor.doc"
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
          :successors="successors"
          :mode="editor.doc?.mode ?? 'parallel'"
          :readonly="editor.readonly"
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

    <el-dialog v-model="issuesVisible" title="校验未通过" width="560px">
      <div class="issue-list">
        <div v-for="issue in editor.issues" :key="issue.id" class="issue">
          <el-tag size="small" :type="issue.level === 'warning' ? 'warning' : 'danger'" effect="plain">
            {{ issue.level === 'warning' ? '提示' : '错误' }}
          </el-tag>
          <span class="issue-text">{{ issue.message }}</span>
          <span v-if="issue.nodeId" class="issue-node">{{ issue.nodeId }}</span>
        </div>
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

.flow-name {
  font-size: 14px;
  font-weight: 600;
}

.banner {
  flex: none;
  border-radius: 0;
}

.warning-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-top: 4px;
}

.issue-level {
  flex: none;
  padding: 0 4px;
  border-radius: 3px;
  font-size: 11px;
  line-height: 16px;
  color: #fff;
  background: var(--el-color-warning);
}

.issue-level.error {
  background: var(--el-color-danger);
}

.warning-item {
  display: flex;
  gap: 8px;
  align-items: baseline;
  padding: 2px 0;
  border: none;
  background: none;
  font: inherit;
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-text-color-regular);
  text-align: left;
  cursor: pointer;
}

.warning-item:hover {
  color: var(--el-color-primary);
}

.warning-node {
  flex: none;
  font-weight: 500;
  color: var(--el-color-warning-dark-2);
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
  font-size: 12px;
  line-height: 1.6;
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
