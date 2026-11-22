"""在 Vue 渲染器中验证跨页面资料详情和撤权后的可见状态。"""
import subprocess
from pathlib import Path


def test_document_selection_and_revocation_close_related_dialogs():
    """通过真实资料组件公开入口、历史按钮和轮询，验证切换请求与撤权的竞态。"""
    script = r"""
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, resolve } from 'node:path'
import { compileFunction } from 'node:vm'
import { parse, compileScript } from 'vue/compiler-sfc'
import { createRenderer, h, nextTick } from 'vue'

const require = createRequire(resolve('package.json'))
const file = resolve('src/features/documents/DocumentManager.vue')
const { descriptor, errors } = parse(readFileSync(file, 'utf8'), { filename: file })
assert.deepEqual(errors, [])
const script = compileScript(descriptor, { id: 'document-test', inlineTemplate: true,
  templateOptions: { compilerOptions: { hoistStatic: false } } })
const { outputFiles } = require('esbuild').buildSync({
  stdin: { contents: script.content, resolveDir: dirname(file), loader: 'ts' },
  format: 'cjs', bundle: true, packages: 'external', platform: 'node', write: false,
})
const compiled = { exports: {} }
compileFunction(outputFiles[0].text, ['module', 'exports', 'require'])(compiled, compiled.exports, require)

function node(tag, text = '') { return { tag, text, parent: null, children: [], props: {} } }
function remove(child) {
  if (child.parent) child.parent.children.splice(child.parent.children.indexOf(child), 1)
  child.parent = null
}
const renderer = createRenderer({
  createElement: tag => node(tag), createText: text => node('text', text),
  createComment: () => node('comment'), parentNode: child => child.parent,
  nextSibling: child => child.parent?.children[child.parent.children.indexOf(child) + 1],
  patchProp: (child, key, _previous, value) => { child.props[key] = value },
  setElementText: (child, text) => { child.children = []; child.text = text },
  setText: (child, text) => { child.text = text }, remove,
  insert(child, parent, anchor) {
    remove(child)
    const index = anchor ? parent.children.indexOf(anchor) : parent.children.length
    parent.children.splice(index, 0, child)
    child.parent = parent
  },
})
function descendants(child) { return [child, ...child.children.flatMap(descendants)] }
const root = node('root')
const tree = () => descendants(root)
const content = child => descendants(child).map(item => item.text).join('')
const titles = () => tree().filter(item => item.tag === 'el-dialog').map(item => item.props.title)
const click = async label => {
  const button = tree().find(item => item.tag === 'el-button' && content(item).trim() === label)
  assert.ok(button, label)
  await button.props.onClick()
  await nextTick()
}

const record = id => ({ id, title: id, body: id + ' 正文', node_id: 'team',
  scope: 'org', level: 1, state: 'published', acl_version: 1, current_version: 1,
  can_write: true, can_download: false })
let releaseFirst
let readable = true
async function api(path) {
  if (path === '/documents/earlier') return new Promise(resolve => { releaseFirst = resolve })
  if (path === '/documents/current/versions') return { items: [{ version: 1, body: '历史正文' }] }
  assert.equal(path, '/documents/current')
  if (!readable) throw new Error('权限已撤回')
  return record('current')
}
const timers = new Map()
globalThis.setInterval = callback => { const key = Symbol(); timers.set(key, callback); return key }
globalThis.clearInterval = key => timers.delete(key)
const app = renderer.createApp(compiled.exports.default, {
  api, libraryVisible: false, refreshWorkspace: async () => {},
  documents: [], organizations: [], rootOrganizationId: 'root', users: [],
  user: { id: 'member', memberships: [], capabilities: { permissions: false, write: true } },
})
const tags = new Set(descriptor.template.content.match(/\bel-[\w-]+/g))
for (const tag of tags) {
  app.component(tag, {
    inheritAttrs: false,
    props: tag === 'el-dialog' ? { modelValue: Boolean } : {},
    setup(props, { attrs, slots }) {
      return () => tag === 'el-dialog' && !props.modelValue
        ? null : h(tag, attrs, slots.default?.())
    },
  })
}
const workspace = app.mount(root)
try {
  const earlier = workspace.openDocument('earlier')
  await workspace.openDocument('current')
  releaseFirst(record('earlier'))
  await earlier
  await nextTick()
  assert.deepEqual(titles(), ['current'])
  assert.ok(content(root).includes('current 正文'))
  assert.ok(!tree().some(item => item.tag === 'el-table'))
  await click('编辑文档')
  await click('历史版本')
  assert.ok(titles().includes('编辑文档') && titles().includes('历史版本记录'))
  assert.ok(content(root).includes('历史正文'))
  assert.equal(timers.size, 1)
  readable = false
  const [poll] = timers.values()
  await poll()
  await nextTick()
  assert.deepEqual(titles(), [])
  assert.ok(!content(root).includes('正文'))
  assert.equal(timers.size, 0)
} finally {
  app.unmount()
}
"""
    subprocess.run(
        ["node", "--input-type=module"], input=script, text=True, check=True,
        cwd=Path(__file__).resolve().parents[1] / "web",
    )
