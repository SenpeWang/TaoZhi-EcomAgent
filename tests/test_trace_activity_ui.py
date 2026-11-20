"""以实际前端展示函数验证行动日志中执行者、结果与工具信息。"""
import shutil
import subprocess
from pathlib import Path


def test_readable_trace_actions():
    root = Path(__file__).resolve().parents[1]
    script = """
import assert from 'node:assert/strict'
import { activityAction, activityResult, actorInstanceLabels } from './web/src/traceActivity.ts'

const actor = {kind: 'agent', span_id: '1000000000000000', display_name: '市场调研员'}
const base = {actor, kind: 'TOOL', name: 'mcp.call_tool', display_name: 'search_web', status: 'ok', error_type: null,
  attributes: {'tool.name': 'search_web', 'tool.status': 'empty', 'tool.result_count': 0}}
assert.deepEqual(activityAction(base), {title: '搜索公开网页', target: 'search_web'})
assert.equal(activityResult(base), '无匹配结果 · 返回 0 条')
assert.equal(activityResult({...base, status: 'error', error_type: 'timeout',
  attributes: {'tool.status':'timeout', 'tool.result_count':0}}), '工具超时')
assert.equal(activityAction({...base, attributes: {'tool.name':'internal_catalog'}}).target, 'internal_catalog')
const model = {...base, kind: 'LLM', attributes: {'llm.model_name': 'qa-model', 'llm.token_count.prompt': 0, 'llm.token_count.completion': 4}}
assert.deepEqual(activityAction(model), {title:'调用模型', target:'qa-model'})
assert.equal(activityResult(model), '输入 0 Token · 输出 4 Token')
assert.equal(activityResult({...model, attributes: {}}), '')
assert.equal(activityResult({...model, status:'error', error_type:'TimeoutError', attributes:{}}), 'TimeoutError')
assert.equal(activityResult({...base, kind:'RETRIEVER', attributes:{'retrieval.result_count':0}}), '检索到 0 条')
const coordinator = {...base, kind:'AGENT', name:'research.orchestrator', attributes:{}}
assert.equal(activityResult(coordinator), '')
for (const [action, expected] of [['dispatch_subagents','已派发 2 项子任务'], ['synthesizer','转入报告撰写'], ['final_node','进入归档']]) {
  assert.equal(activityResult({...coordinator, attributes:{'research.next_action':action, 'research.dispatched_count':2}}), expected)
}
assert.equal(actorInstanceLabels([base, model, {...model, actor:null}]).size, 0)
const other = {...base, actor:{...actor, span_id:'1000000000000001'}}
const labels = actorInstanceLabels([base, model, other])
assert.equal(labels.get(actor.span_id), actor.span_id)
assert.equal(labels.get(other.actor.span_id), other.actor.span_id)
"""
    result = subprocess.run(
        [shutil.which("node") or "node", "--experimental-strip-types", "--input-type=module"],
        input=script, cwd=root, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
