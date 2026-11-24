"""验证 Markdown 原件渲染的排版与安全清洗。"""
import subprocess
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


def test_markdown_renderer_keeps_structure_and_strips_unsafe_html():
    """标题、表格与外部链接保留，内嵌脚本、脚本协议和远程图片被清除。"""
    script = r"""
import assert from 'node:assert/strict'
import { renderMarkdown } from './web/src/features/documents/markdown.ts'

const html = renderMarkdown('# 标题\n\n| 型号 | 功率 |\n| --- | --- |\n| A | 20W |\n\n**加粗** 与 [官网](https://example.org/spec)')
assert.ok(html.includes('<h1>标题</h1>'))
assert.ok(html.includes('<table>') && html.includes('<td>A</td>'))
assert.ok(html.includes('rel="noopener noreferrer"'))

const unsafe = renderMarkdown('<script>alert(1)</script>\n\n[危险](javascript:alert(1))\n\n![图](https://example.org/a.png)')
assert.ok(!unsafe.includes('<script>'))
assert.ok(!unsafe.includes('javascript:'))
assert.ok(!unsafe.includes('<img'))
assert.ok(unsafe.includes('&lt;script&gt;'))
"""
    subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module"],
        input=script, cwd=PROJECT, text=True, check=True,
    )
