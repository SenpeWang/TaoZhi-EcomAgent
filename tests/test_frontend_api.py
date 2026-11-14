"""同源 API 边界验收，使用真实 TS 请求模块和 Node 24；无网络或真实账号。"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


def test_session_csrf_and_unknown_response_contracts():
    """验证写请求、畸形身份、失效会话和网络失败的可观察行为。"""
    runtime = Path.home() / ".local/share/ecom-agent/runtime/node/bin/node"
    executable = str(runtime) if runtime.is_file() else "node"
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
    runtime = Path.home() / ".local/share/ecom-agent/runtime/node/bin/node"
    executable = str(runtime) if runtime.is_file() else "node"
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
