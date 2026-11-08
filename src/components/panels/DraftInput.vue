<script setup lang="ts">
import { ref, watch } from 'vue'

/**
 * 提交式文本输入：编辑时只改本地草稿，失焦或回车才写回模型。
 *
 * 为什么不直接 `<el-input :model-value @change>`：Element Plus 每次 input 之后
 * 都会把原生输入框的值重新写回 props.modelValue。父层没有改 prop 时，用户刚敲的
 * 字会被立刻抹掉，表现就是「这一格改不了」。草稿模式让显示值先跟手，提交再落到文档。
 */
const props = withDefaults(
  defineProps<{
    modelValue: string
    readonly?: boolean
    placeholder?: string
    type?: 'text' | 'textarea'
    rows?: number
    /** 父层拒收（重名等）时是否把显示改回真实值；JSON 这类允许半截文本的填 false */
    revert?: boolean
  }>(),
  { type: 'text', rows: 2, revert: true },
)
const emit = defineEmits<{ (e: 'update:modelValue', value: string): void }>()

const draft = ref(props.modelValue)
const focused = ref(false)

watch(
  () => props.modelValue,
  (value) => {
    // 正在输入时不抢用户的文本；失焦后以父层的值为准
    if (!focused.value && value !== draft.value) draft.value = value
  },
)

function onInput(value: string | number): void {
  draft.value = String(value ?? '')
}

function commit(): void {
  focused.value = false
  const text = draft.value
  if (text === props.modelValue) return
  emit('update:modelValue', text)
  if (!props.revert) return
  // 父层可能拒收（标识重名、空值），下一拍按真实值回填
  window.setTimeout(() => {
    if (props.modelValue !== text) draft.value = props.modelValue
  }, 0)
}
</script>

<template>
  <el-input
    :model-value="draft" :disabled="readonly" :type="type" :rows="rows" size="small"
    :placeholder="placeholder"
    @focus="focused = true"
    @update:model-value="onInput"
    @change="commit"
    @keyup.enter="commit"
  />
</template>
