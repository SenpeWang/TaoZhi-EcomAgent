"""通过真实的 TypeScript 展示策略和已构建页面核验知识问答工作区。"""
import os
import shutil
import subprocess
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest


PROJECT = Path(__file__).resolve().parents[1]


def test_knowledge_answer_presentation():
    script = """
import assert from 'node:assert/strict'
import { answerBasisLabel, citationGroups, publicSourceUrl, visibleTaskResult } from './web/src/knowledgeAnswers.ts'
const answer = {answer:'已核验结论'}
const snapshot = {state:'completed', can_review:false, restricted:false, result:answer}
assert.equal(visibleTaskResult(snapshot),answer)
assert.equal(visibleTaskResult({...snapshot,state:'review',can_review:true}),answer)
for (const state of ['review','running','rejected','cancelled','failed']) {
  assert.equal(visibleTaskResult({...snapshot,state}),null)
}
assert.equal(visibleTaskResult({...snapshot,restricted:true,can_review:true}),null)
assert.equal(answerBasisLabel(undefined), '')
assert.equal(answerBasisLabel('__proto__'), '')
assert.equal(answerBasisLabel('source_excerpt'), '授权原文摘录')
assert.equal(answerBasisLabel('business_snapshot'), '实时业务快照')
const groups = citationGroups({citations:[{}, {cited:true}, {cited:false}]})
assert.deepEqual(groups.map(group => [group.label, group.entries.map(item => item.number)]),
  [['答复引用',[2]],['相关资料',[1,3]]])
for (const url of ['javascript:void(0)','data:text/plain,test','//example.org','http://example.org','https://user@example.org']) {
  assert.equal(publicSourceUrl(url),'')
}
assert.equal(publicSourceUrl('https://www.usb.org/usb-charger-pd'),'https://www.usb.org/usb-charger-pd')
"""
    subprocess.run(["node", "--experimental-strip-types", "--input-type=module"], input=script,
                   cwd=PROJECT, text=True, check=True)


def test_knowledge_workspace_in_browser(tmp_path):
    """用实际生产构建和测试 API 响应验证提交选项、来源分组、任务切换及受限状态。"""
    dist = PROJECT / "web/dist"
    browser = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
    if not browser or not (dist / "index.html").is_file():
        message = "问答页面交互验收需要现有的 Chrome 和 web/dist 构建产物"
        if os.environ.get("GITHUB_ACTIONS") == "true":
            pytest.fail(message)
        pytest.skip(message)
    site = tmp_path / "site"
    site.mkdir()
    (site / "assets").symlink_to(dist / "assets", target_is_directory=True)
    html = (dist / "index.html").read_text()
    (site / "index.html").write_text(html.replace("<head>", '<head><script src="/fixture.js"></script>', 1))
    shutil.copyfile(Path(__file__).with_name("knowledge_workspace.browser.js"), site / "fixture.js")
    with ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=site)) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = subprocess.run([
                browser, "--headless", "--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage",
                f"--user-data-dir={tmp_path / 'profile'}", "--disable-background-networking",
                "--virtual-time-budget=12000", "--dump-dom", f"http://127.0.0.1:{server.server_port}/",
            ], capture_output=True, text=True, timeout=50, check=True)
        finally:
            server.shutdown()
            thread.join()
    assert 'data-knowledge-test="passed"' in result.stdout, result.stdout[-3500:]
