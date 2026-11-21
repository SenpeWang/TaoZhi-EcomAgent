"""同源 API 边界验收，使用真实 TS 请求模块和 Node 24；无网络或真实账号。"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


def _node() -> str:
    return shutil.which("node") or "node"


def test_session_csrf_and_unknown_response_contracts():
    """验证写请求、畸形身份、失效会话和网络失败的可观察行为。"""
    executable = _node()
    module = (PROJECT / "web/src/api.ts").as_uri()
    source = """
import assert from 'node:assert/strict'
import { requestApi } from MODULE
globalThis.document = { cookie: 'ecom_csrf_demo=example%20csrf; ecom_csrf_production=other' }
let cleared = 0
const clear = () => { cleared += 1 }
const calls = []
globalThis.fetch = async (url, options) => {
  calls.push({ url, options })
  return new Response(JSON.stringify({ items: [] }), { status: 200 })
}
await requestApi('/documents', 'demo', clear)
assert.equal(calls[0].url, '/api/documents')
assert.equal(calls[0].options.credentials, 'same-origin')
assert.equal(calls[0].options.headers['X-CSRF-Token'], undefined)
await requestApi('/documents', 'demo', clear, 'POST', { title: '合成资料' })
assert.equal(calls[1].options.headers['X-CSRF-Token'], 'example csrf')
assert.equal(calls[1].options.headers['Content-Type'], 'application/json')
assert.equal(JSON.parse(calls[1].options.body).title, '合成资料')
globalThis.fetch = async () => new Response(JSON.stringify({ id: 'created_task' }), { status: 201 })
assert.deepEqual(await requestApi('/tasks', 'demo', clear, 'POST', { question: '合成问题' }), { id: 'created_task' })
globalThis.fetch = async (url, options) => {
  calls.push({ url, options })
  return new Response(JSON.stringify({ items: [] }), { status: 200 })
}
const form = new FormData()
form.append('title', '合成附件')
await requestApi('/documents/upload', 'demo', clear, 'POST', form)
assert.equal(calls[2].options.body, form)
assert.equal(calls[2].options.headers['Content-Type'], undefined)
globalThis.fetch = async () => new Response('invalid-json', { status: 401 })
await assert.rejects(requestApi('/auth/me', 'demo', clear), /服务响应异常/)
assert.equal(cleared, 1)
globalThis.fetch = async () => new Response(JSON.stringify({ message: '无权读取' }), { status: 403 })
await assert.rejects(requestApi('/documents/unknown', 'demo', clear), /无权读取/)
assert.equal(cleared, 1)
for (const data of [{}, { user: null }, { user: { id: 'fake', roles: ['老板'], capabilities: {} } }]) {
  globalThis.fetch = async () => new Response(JSON.stringify(data), { status: 200 })
  await assert.rejects(requestApi('/auth/login', 'demo', clear), /登录身份响应异常/)
}
const ownerPath = '/admin/traces/owners?q=demo'
const owner = { id: 'user_demo', username: 'demo', display_name: '示例用户' }
const ownerPage = { items: [owner], next_cursor: 'user_demo' }
globalThis.fetch = async () => Response.json(ownerPage)
assert.deepEqual(await requestApi(ownerPath, 'demo', clear), ownerPage)
for (const malformed of [
  { items: [owner], next_cursor: 1 },
  { items: [{ id: 'user_demo', display_name: '示例用户' }], next_cursor: null },
]) {
  globalThis.fetch = async () => Response.json(malformed)
  await assert.rejects(requestApi(ownerPath, 'demo', clear), /响应异常/)
}
const traceId = 'a'.repeat(32)
const timestamp = '2026-01-01T08:00:00Z'
const run = { trace_id: traceId, run_version: 1, status: 'ok', started_at: timestamp,
  ended_at: timestamp, kind_counts: { LLM: 2 }, category_counts: { LLM: 2 }, span_count: 2, failed_count: 0, input_tokens: 10, output_tokens: 5 }
const runPath = '/admin/traces/task/runs?trace_id=' + traceId
globalThis.fetch = async () => Response.json({ items: [run], next_cursor: null })
assert.deepEqual((await requestApi(runPath, 'demo', clear)).items, [run])
for (const kindCounts of [undefined, { UNKNOWN: 2 }, { LLM: -1 }]) {
  globalThis.fetch = async () => Response.json({ items: [{ ...run, kind_counts: kindCounts }], next_cursor: null })
  await assert.rejects(requestApi(runPath, 'demo', clear), /响应异常/)
}
const spanPath = '/admin/traces/task/runs/' + traceId + '/spans?kind=LLM'
const span = { trace_id: traceId, span_id: '1'.repeat(16), parent_span_id: null, name: 'llm.chat',
  display_name: '模型调用', started_at: timestamp, ended_at: timestamp, status: 'ok', kind: 'LLM', category: 'LLM',
  duration_ms: 0, error_type: null, attributes: { 'llm.model_name': 'demo-1' } }
globalThis.fetch = async () => Response.json({ items: [span], next_cursor: '2', observed_at: timestamp })
assert.deepEqual((await requestApi(spanPath, 'demo', clear)).items, [span])
for (const invalid of [{ trace_id: 'b'.repeat(32) }, { kind: 'TOOL' }, { kind: 'MODEL' }, { display_name: null }]) {
  globalThis.fetch = async () => Response.json({ items: [{ ...span, ...invalid }], next_cursor: null, observed_at: timestamp })
  await assert.rejects(requestApi(spanPath, 'demo', clear), /响应异常/)
}
const activityPath = '/admin/traces/task/runs/' + traceId + '/activities?category=LLM'
const actor = { kind: 'agent', span_id: '2'.repeat(16), display_name: '采购分析师' }
for (const source of [actor, { ...actor, kind: 'workflow' }, null]) {
  const item = { ...span, actor: source }
  globalThis.fetch = async (url) => {
    assert.equal(url, '/api' + activityPath)
    return Response.json({ items: [item], next_cursor: 'cursor', observed_at: timestamp })
  }
  assert.deepEqual((await requestApi(activityPath, 'demo', clear)).items, [item])
}
for (const invalid of [{ actor: undefined }, { actor: { ...actor, span_id: 'broken' } },
                       { actor: { ...actor, kind: 'machine' } }, { kind: 'TOOL' }, { category: 'TOOL' }, { category: 'UNKNOWN' }, { trace_id: 'b'.repeat(32) }]) {
  globalThis.fetch = async () => Response.json({ items: [{ ...span, actor, ...invalid }], next_cursor: null, observed_at: timestamp })
  await assert.rejects(requestApi(activityPath, 'demo', clear), /响应异常/)
}
for (const data of [{}, { items: [null] }, { items: [1] }, [], null]) {
  globalThis.fetch = async () => new Response(JSON.stringify(data), { status: 200 })
  await assert.rejects(requestApi('/documents', 'demo', clear), /响应异常/)
}
globalThis.fetch = async () => { throw new Error('private transport detail') }
await assert.rejects(requestApi('/documents', 'demo', clear), /暂时无法连接工作台/)
assert.equal(cleared, 1)
"""
    source = source.replace("MODULE", json.dumps(module))
    result = subprocess.run(
        [executable, "--experimental-strip-types", "--input-type=module"],
        input=source, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_markdown_report_rendering_and_xss_safety():
    """验证调研报告 Markdown 渲染器支持标题目录、表格、提示块与 XSS 防护。"""
    executable = _node()
    module = (PROJECT / "web/src/markdown.ts").as_uri()
    source = """
import assert from 'node:assert/strict'
import { renderMarkdownReport } from MODULE

const sample = `# 无线充电器调研报告

## 核心结论速览
主力零售价格带集中在 **69-129 元**，建议优先切入磁吸桌面款。

| 渠道 | 主流售价 | 起订量 |
| --- | :---: | ---: |
| 1688 源头厂 | 28.5 元 | 50 |
| 全网零售 | 89 元 | 1 |

> ⚠️ 数据缺口与风险提示：暂无线下商超进货折扣样本，参考 [行业报告](https://example.com/report) 与 [危险链接](javascript:alert(1))。

<script>alert('xss')</script>
`

const res = renderMarkdownReport(sample, 'test-sec')
assert.equal(res.headings.length, 2)
assert.equal(res.headings[0].id, 'test-sec-0')
assert.equal(res.headings[1].text, '核心结论速览')
assert.ok(res.html.includes('<h1 id="test-sec-0"'))
assert.ok(res.html.includes('<h2 id="test-sec-1"'))
assert.ok(res.html.includes('<strong>69-129 元</strong>'))
assert.ok(res.html.includes('<table class="md-table">'))
assert.ok(res.html.includes('text-align:center'))
assert.ok(res.html.includes('text-align:right'))
assert.ok(res.html.includes('md-callout--warn'))
assert.ok(res.html.includes('href="https://example.com/report"'))
assert.ok(!res.html.includes('javascript:'))
assert.ok(!res.html.includes('<script>'))
assert.ok(res.html.includes('&lt;script&gt;alert(&#39;xss&#39;)&lt;/script&gt;'))
"""
    source = source.replace("MODULE", json.dumps(module))
    result = subprocess.run(
        [executable, "--experimental-strip-types", "--input-type=module"],
        input=source, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_trace_navigation_and_retry_analysis():
    """验证成员的轨迹入口、强制改密限制及成功执行中的异常子调用定位。"""
    source = """
import assert from 'node:assert/strict'
import { workspaceNavigation } from './web/src/workspaceNavigation.ts'
import { analysisRows, buildWaterfall, runDuration, spanFields, spanSecondaryName } from './web/src/traceWaterfall.ts'

const capabilities = { system: true, permissions: true, review: true, write: true }
function pages(user) {
  return workspaceNavigation(user, capabilities, 0, 0)
    .flatMap(group => group.items.map(item => item.page))
}
assert.ok(pages({ system_admin: true }).includes('轨迹中台'))
assert.ok(pages({ system_admin: false, is_boss: true }).includes('轨迹中台'))
const memberCapabilities = { system: false, permissions: false, review: false, write: false }
const memberNavigation = workspaceNavigation({ system_admin: false }, memberCapabilities, 0, 0)
assert.ok(memberNavigation.some(group => group.items.some(item => item.page === '轨迹中台')))
assert.deepEqual(pages(null), [])
assert.deepEqual(pages({ system_admin: true, must_change: true }), ['个人中心'])

const started = '2026-01-01T08:00:00Z'
const ended = '2026-01-01T08:00:01Z'
const run = { started_at: started, ended_at: ended, status: 'ok' }
const root = { span_id: 'root', parent_span_id: null, status: 'ok',
  started_at: started, ended_at: ended, duration_ms: 1000 }
const failed = { ...root, span_id: 'failed', parent_span_id: 'root', status: 'error',
  ended_at: '2026-01-01T08:00:00.300Z', duration_ms: 300 }
const recovered = { ...root, span_id: 'recovered', parent_span_id: 'root',
  started_at: failed.ended_at, duration_ms: 700 }
const interrupted = { ...root, span_id: 'interrupted', parent_span_id: 'root',
  status: 'interrupted', ended_at: null, duration_ms: null }
const { rows } = buildWaterfall(run, [root, failed, interrupted, recovered], new Set(), Date.parse(ended))
assert.deepEqual(analysisRows(rows, 'latency').map(row => row.span.span_id), ['recovered', 'failed'])
assert.deepEqual(analysisRows(rows, 'issues').map(row => row.span.span_id), ['failed', 'interrupted'])
const observed = Date.parse(ended) + 1000
const flat = buildWaterfall({ ...run, status: 'running', ended_at: null }, [failed, recovered], new Set(), observed, true)
assert.equal(flat.total, 2000)
assert.ok(flat.rows.every(row => !row.hasChildren && row.depth === 0))
assert.equal(flat.rows[0].span.parent_span_id, 'root')
const llm = { kind: 'LLM', attributes: { 'llm.model_name': 'demo-2', 'llm.endpoint_id': 'secondary',
  'llm.token_count.prompt': 0, 'llm.token_count.completion': 3, 'llm.token_count.total': 3 } }
assert.equal(spanSecondaryName(llm), 'demo-2')
assert.equal(spanSecondaryName({ ...llm, display_name: 'demo-2' }), '')
assert.deepEqual(spanFields(llm).map(({ value }) => value), ['demo-2', 'secondary', 0, 3, 3])
for (const [kind, key, value] of [
  ['TOOL', 'tool.name', 'search'], ['RETRIEVER', 'retrieval.result_count', 0], ['AGENT', 'agent.name', 'research'],
  ['EMBEDDING', 'embedding.model_name', 'embed'], ['RERANKER', 'reranker.model_name', 'rank'],
]) {
  const fields = spanFields({ kind, attributes: { [key]: value } })
  assert.equal(fields.length, 1)
  assert.equal(fields[0].value, value)
}
assert.equal(runDuration({ ...run, status: 'interrupted', ended_at: null }, Date.parse(ended)), null)
"""
    result = subprocess.run(
        [_node(), "--experimental-strip-types", "--input-type=module"],
        input=source, cwd=PROJECT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
