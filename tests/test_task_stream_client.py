"""用浏览器订阅函数验证实时结果、断线重放与执行版本隔离。"""
import subprocess
from pathlib import Path


def test_live_result_draft_follows_authorized_run():
    script = """
import assert from 'node:assert/strict'
import { openTaskEventStream } from './web/src/shared/taskStream.ts'
let stream
class Source extends EventTarget {
  static CLOSED = 2
  constructor(url) {
    super()
    assert.equal(url, '/api/tasks/turn%2F1/events')
    stream = this
  }
  close() { this.readyState = Source.CLOSED }
  receive(type, data) {
    const event = new MessageEvent(type, {data: JSON.stringify(data)})
    if (type === 'message') this.onmessage(event)
    else this.dispatchEvent(event)
  }
}
globalThis.EventSource = Source
const drafts = [], progress = [], done = []
let errors = 0
const close = openTaskEventStream('turn/1', {
  onMessage: (row) => progress.push(row.stage),
  onResult: (draft) => drafts.push(draft),
  onDone: () => done.push('completed'),
  onError: () => errors++,
})
const delta = (id, text, run_version = 1) => stream.receive('result_delta', {id, text, run_version})
stream.receive('message', {id: 1, run_version: 1, stage: '开始撰写'})
delta(2, '市场')
assert.equal(drafts.at(-1), '市场')
stream.readyState = 0
stream.onerror()
assert.equal(errors, 0)
assert.equal(drafts.at(-1), '')
stream.receive('result_resume', {run_version: 2})
assert.equal(drafts.at(-1), '')
stream.receive('result_resume', {run_version: 1})
assert.equal(drafts.at(-1), '市场')
delta(2, '市场')
delta(3, '调研')
assert.equal(drafts.at(-1), '市场调研')
stream.receive('result_reset', {id: 4, run_version: 1})
assert.equal(drafts.at(-1), '')
delta(5, '替代结果')
stream.receive('result_reset', {id: 4, run_version: 1})
assert.equal(drafts.at(-1), '替代结果')
stream.receive('message', {id: 6, run_version: 2, stage: '重试'})
delta(7, '过期', 1)
assert.equal(drafts.at(-1), '')
delta(8, '新结果', 2)
stream.receive('result_reset', {run_version: 2})
assert.equal(drafts.at(-1), '')
stream.receive('done', {id:'turn/1', run_version: 1})
stream.receive('done', {id:'another-turn', run_version: 2})
assert.deepEqual(done, [])
stream.receive('done', {id:'turn/1', run_version: 2})
delta(9, '迟到', 2)
assert.equal(drafts.at(-1), '')
assert.deepEqual(progress, ['开始撰写', '重试'])
assert.deepEqual(done, ['completed'])
assert.equal(stream.readyState, Source.CLOSED)
close()
"""
    subprocess.run(["node", "--experimental-strip-types", "--input-type=module", "-e", script],
                   cwd=Path(__file__).resolve().parents[1], check=True)
