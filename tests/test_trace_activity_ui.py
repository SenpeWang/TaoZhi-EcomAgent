"""以实际前端展示函数验证行动日志中执行者、结果与工具信息。"""
import shutil
import subprocess
from pathlib import Path


def test_readable_trace_actions():
    root = Path(__file__).resolve().parents[1]
    script = """
import assert from 'node:assert/strict'
import { activityCategoryLabels, spanFields } from './web/src/traceWaterfall.ts'
import { activityAction, activityResult, actorInstanceLabels } from './web/src/traceActivity.ts'

const actor = {kind: 'agent', span_id: '1000000000000000', display_name: '市场调研员'}
const base = {actor, kind: 'TOOL', name: 'mcp.call_tool', display_name: 'search_web', status: 'ok', error_type: null,
  attributes: {'tool.name': 'search_web', 'tool.status': 'empty', 'tool.result_count': 0}}
assert.deepEqual(activityAction(base), {title: '搜索公开网页', target: 'search_web'})
assert.deepEqual(activityAction({...base, attributes: {'tool.name':'get_current_time'}}),
  {title: '读取当前时间', target: 'get_current_time'})
assert.equal(activityResult(base), '无匹配结果 · 返回 0 条')
assert.equal(activityResult({...base, status: 'error', error_type: 'timeout',
  attributes: {'tool.status':'timeout', 'tool.result_count':0}}), '工具超时')
assert.equal(activityAction({...base, attributes: {'tool.name':'internal_catalog'}}).target, 'internal_catalog')
for (const [dispatched, status, expected] of [
  [false, 'cancelled', '未派发 · 派发前已取消'], [false, 'timeout', '未派发 · 派发前已超时'],
  [true, 'timeout', '已派发 · 工具超时'], [true, 'error', '已派发 · 工具执行失败'],
]) {
  const node = {...base, status:'error', error_type:status,
    attributes:{'tool.dispatched':dispatched,'tool.status':status,'tool.result_count':0}}
  assert.equal(activityResult(node),expected)
  assert.equal(spanFields(node).find(item => item.key === 'tool.dispatched').value,dispatched ? '是' : '否')
}
const model = {...base, kind: 'LLM', attributes: {'llm.model_name': 'qa-model', 'llm.token_count.prompt': 0, 'llm.token_count.completion': 4}}
assert.deepEqual(activityAction(model), {title:'调用模型', target:'qa-model'})
assert.equal(activityResult(model), '输入 0 Token · 输出 4 Token')
assert.equal(activityResult({...model, attributes: {}}), '')
assert.equal(activityResult({...model, status:'error', error_type:'TimeoutError', attributes:{}}), 'TimeoutError')
assert.equal(activityResult({...base, kind:'RETRIEVER', attributes:{'retrieval.result_count':0}}), '检索到 0 条')
assert.equal(activityCategoryLabels.TOOL, '工具调用')
assert.equal(activityCategoryLabels.CONTEXT, '上下文处理')
const context = { ...base, category: 'CONTEXT', kind: 'CHAIN', name: 'context.prepare_report',
  display_name: '组装报告证据上下文', attributes: {'context.evidence_count': 0, 'context.output_chars': 320} }
assert.equal(activityAction(context).title, '组装报告证据上下文')
assert.equal(activityResult(context), '0 份证据 · 320 字符')
assert.equal(activityResult({...context, attributes:{'context.input_chars':2000,'context.output_chars':1500}}), '2000 → 1500 字符')
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
