<script setup lang="ts">
import { computed, reactive, watch } from 'vue'
import type { ReviewTask } from '@/domain/types'
import { parameterEmpty } from '@/domain/parameters'
import ParamValueInput from '@/components/panels/ParamValueInput.vue'
import AttachmentList from '@/components/panels/AttachmentList.vue'

const props = defineProps<{ review: ReviewTask; busy?: boolean; error?: string }>()
const emit = defineEmits<{ (e: 'submit', data: Record<string, unknown>): void }>()
const values = reactive<Record<string, unknown>>({})
const errors = reactive<Record<string, string>>({})
const invalid = reactive<Record<string, boolean>>({})
const blocked = computed(() => Object.values(invalid).some(Boolean))

watch(() => props.review.id, () => {
  for (const name of Object.keys(values)) delete values[name]
  for (const name of Object.keys(errors)) delete errors[name]
  for (const name of Object.keys(invalid)) delete invalid[name]
  for (const field of props.review.request.fields ?? []) values[field.name] = field.default
}, { immediate: true })

function submit(): void {
  for (const name of Object.keys(errors)) delete errors[name]
  const data: Record<string, unknown> = {}
  for (const field of props.review.request.fields ?? []) {
    const value = values[field.name]
    if (parameterEmpty(value, field.type)) {
      if (field.required !== false) errors[field.name] = '请提供此项数据'
      continue
    }
    if (invalid[field.name]) errors[field.name] = '请先修正输入或完成上传'
    else data[field.name] = value
  }
  if (!blocked.value && !Object.keys(errors).length) emit('submit', data)
}
</script>

<template>
  <div class="data-task">
    <strong>{{ review.request.title }}</strong>
    <p>{{ review.request.instructions }}</p>
    <p v-if="review.request.detail">{{ review.request.detail }}</p>
    <details v-if="review.request.pipeline?.inputs">
      <summary>已绑定的输入</summary><pre>{{ JSON.stringify(review.request.pipeline.inputs, null, 2) }}</pre>
    </details>
    <el-form label-position="top" @submit.prevent="submit">
      <el-form-item v-for="field in review.request.fields" :key="`${review.id}:${field.name}`"
        :label="`${field.label || field.name}${field.unit ? `（${field.unit}）` : ''}`"
        :required="field.required !== false" :error="errors[field.name]" :for="`data-${field.name}`">
        <ParamValueInput :id="`data-${field.name}`" :type="field.type" :model-value="values[field.name]"
          :min="field.min" :max="field.max"
          :readonly="busy" @update:model-value="values[field.name] = $event"
          @validity="invalid[field.name] = !$event" />
        <div v-if="field.description" class="hint">{{ field.description }}</div>
      </el-form-item>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <el-button type="primary" native-type="submit" :loading="busy" :disabled="blocked">
        {{ review.request.type === 'parameters' ? '提交参数并执行' : '提交数据并解析' }}
      </el-button>
    </el-form>
    <AttachmentList v-if="review.request.attachments?.length" :attachments="review.request.attachments" dense />
  </div>
</template>

<style scoped>
.data-task { display: grid; gap: 10px; }
p { margin: 0; white-space: pre-wrap; font-size: 13px; line-height: 1.6; }
.hint { width: 100%; color: var(--el-text-color-secondary); font-size: 12px; }
.error { color: var(--el-color-danger); }
pre { overflow: auto; max-height: 160px; font-size: 12px; }
</style>
