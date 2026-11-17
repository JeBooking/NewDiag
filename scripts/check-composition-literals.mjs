import fs from 'node:fs/promises'
import assert from 'node:assert/strict'
import { fileURLToPath, pathToFileURL } from 'node:url'
import path from 'node:path'
import { randomUUID } from 'node:crypto'
import { parse, compileScript } from 'vue/compiler-sfc'
import ts from 'typescript'
import { createRenderer, h, ref, nextTick, defineComponent } from 'vue'

// 只验证组件的受控输入契约，不代替真实浏览器和 Element Plus 的交互验收。
const rootPath = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const sourceFile = path.resolve(rootPath,
  process.argv[2] ?? 'src/components/panels/CompositionArgumentEditor.vue')
const source = await fs.readFile(sourceFile, 'utf8')
const compiled = compileScript(parse(source, { filename: sourceFile }).descriptor, {
  id: 'literal-check', inlineTemplate: true,
})
const output = ts.transpileModule(compiled.content, { compilerOptions: {
  target: ts.ScriptTarget.ESNext, module: ts.ModuleKind.ESNext,
} }).outputText
const cache = path.join(rootPath, 'backend/.pytest_cache')
await fs.mkdir(cache, { recursive: true })
const compiledFile = pathToFileURL(path.join(cache, `literal-component-${randomUUID()}.mjs`))
await fs.writeFile(compiledFile, output)
let Editor
try {
  Editor = (await import(compiledFile.href)).default
} finally {
  await fs.unlink(compiledFile)
}

function element(type, text = '') {
  return { type, text, props: {}, children: [], parent: null }
}
const renderer = createRenderer({
  createElement: (type) => element(type),
  createText: (text) => element('#text', text),
  createComment: (text) => element('#comment', text),
  setText: (node, text) => { node.text = text },
  setElementText: (node, text) => {
    node.text = text
    node.children = []
  },
  parentNode: (node) => node.parent,
  nextSibling: (node) => node.parent?.children[node.parent.children.indexOf(node) + 1] ?? null,
  insert(node, parent, anchor) {
    if (node.parent) node.parent.children.splice(node.parent.children.indexOf(node), 1)
    const index = anchor ? parent.children.indexOf(anchor) : -1
    if (index >= 0) parent.children.splice(index, 0, node)
    else parent.children.push(node)
    node.parent = parent
  },
  remove(node) {
    if (node.parent) node.parent.children.splice(node.parent.children.indexOf(node), 1)
    node.parent = null
  },
  patchProp: (node, key, _previous, next) => { node.props[key] = next },
})
const Input = defineComponent({
  props: ['modelValue', 'disabled'],
  emits: ['update:modelValue', 'change'],
  setup(props, { emit }) {
    return () => h('input', {
      value: props.modelValue, disabled: props.disabled,
      onInput: (value) => emit('update:modelValue', value),
    })
  },
})
const Wrapper = defineComponent({
  setup(_, { slots }) { return () => h('span', {}, slots.default?.()) },
})
const value = ref({ lit: null })
const readonly = ref(false)
const root = element('root')
const app = renderer.createApp({
  setup() {
    return () => h(Editor, {
      value: value.value, composition: { objects: [], resources: [], operations: [] },
      readonly: readonly.value, onUpdate: (next) => { value.value = structuredClone(next) },
    })
  },
})
for (const name of ['ElSelect', 'ElOption', 'ElButton', 'ElCheckbox']) app.component(name, Wrapper)
app.component('ElInput', Input)
app.component('CompositionArgumentEditor', Editor)
app.mount(root)

function find(node, predicate) {
  if (predicate(node)) return node
  for (const child of node.children) {
    const found = find(child, predicate)
    if (found) return found
  }
}
function input() { return find(root, (node) => node.type === 'input') }
async function type(text, expected) {
  input().props.onInput(text)
  await nextTick()
  assert.equal(input().props.value, text)
  assert.deepEqual(value.value, { lit: expected })
}
try {
  await type('12', 12)
  await type('"', 12)
  assert.match(find(root, (node) => node.type === 'p')?.text ?? '', /尚未保存/)
  await type('"hello"', 'hello')
  await type('[1,', 'hello')
  await type('[1,2]', [1, 2])
  await type('{"peak":8}', { peak: 8 })
  await type('false', false)
  await type('null', null)
  await type('1.', null)
  await type('1.25', 1.25)
  value.value = { lit: 99 }
  await nextTick()
  assert.equal(input().props.value, '99')
  readonly.value = true
  await nextTick()
  assert.equal(input().props.disabled, true)
  readonly.value = false
  value.value = { fields: { threshold: { lit: 5 } } }
  await nextTick()
  input().props.onInput('8')
  await nextTick()
  assert.deepEqual(value.value, { fields: { threshold: { lit: 8 } } })
  console.log('固定值组件回归通过：JSON 类型、未完成草稿、外部更新、只读及嵌套字段')
} finally {
  app.unmount()
}
