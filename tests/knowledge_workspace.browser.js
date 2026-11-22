const makeTask = (id, question, result, state = 'completed') => ({
  id, question, result, state, kind: 'ask', owner_id: 'reader', restricted: false,
  can_review: false, can_cancel: false, input_level: 1, stage: '', progress: 0,
  created_at: '2026-01-10T08:00:00Z',
})
const cited = (id, title, used) => ({
  chunk_id: id, document_id: id, title, quote: title, page: 1, version: 2, cited: used,
})
const internal = {answer_basis: 'internal_evidence', answer: '测试已核验正文 [2]', citations: [
  cited('c1', '测试相关文件', false), cited('c2', '测试依据文件', true),
]}
const sources = [
  {title: 'USB-IF 原文', url: 'https://www.usb.org/usb-charger-pd'},
  {title: '无效链接不应显示', url: 'data:text/plain,not-a-source'},
]
const tasks = [
  makeTask('internal', '合成内部问题', internal),
  makeTask('excerpt', '合成原文问题', {answer_basis: 'source_excerpt', answer: '可人工核实的摘录', missing: ['合成缺口']}),
  makeTask('public', '合成通用问题', {answer_basis: 'public_reference', answer: '通用释义', public_sources: sources}),
  makeTask('gap', '合成待补资料问题', {answer_basis: 'insufficient_evidence', answer: '请补充依据', guidance: '请确认政策版本'}),
  makeTask('review', '合成待审问题', {answer: '不得展示的审核草稿'}, 'review'),
  {...makeTask('restricted', '不得展示的受限题名', internal), restricted: true},
  {...makeTask('other', '其他人的合成提问', null), owner_id: 'other'},
  makeTask('running', '合成流式问题', null, 'running'),
]
const user = {
  id: 'reader', username: 'reader', display_name: '合成员工', role_level: 1, roles: ['员工'],
  memberships: [], is_boss: false, is_leader: false, must_change: false,
  capabilities: {system: false, write: false, review: false, permissions: false},
}
let selectedStream
let submitted
let releaseOldDetail
let delayInternal = false
window.EventSource = class extends EventTarget {
  constructor() { super(); selectedStream = this }
  close() { this.closed = true }
}
const originalFetch = window.fetch.bind(window)
window.fetch = async (url, init = {}) => {
  if (typeof url !== 'string' || !url.startsWith('/api/')) return originalFetch(url, init)
  if (url === '/api/auth/me') return Response.json(user)
  if (url === '/api/info') return Response.json({mode: 'test', name: '合成工作区', tenant_code: 'test'})
  if (url === '/api/dashboard') return Response.json({teams: [], documents: 0, tasks: tasks.length, review: 0, completed: 4})
  if (url === '/api/tasks' && init.method === 'POST') {
    submitted = JSON.parse(init.body)
    if (submitted.question.includes('补充渠道')) {
      tasks.push(makeTask('followup', submitted.question, {answer_basis: 'insufficient_evidence', answer: '新增条件已提交'}))
      return Response.json({id: 'followup'}, {status: 201})
    }
    return Response.json({id: 'running'}, {status: 201})
  }
  if (url === '/api/tasks') return Response.json({items: tasks})
  if (url.startsWith('/api/tasks/')) {
    if (url === '/api/tasks/internal' && delayInternal) await new Promise(resolve => { releaseOldDetail = resolve })
    return Response.json(tasks.find(task => url.endsWith('/' + task.id)))
  }
  return Response.json({items: []})
}

const button = (label) => [...document.querySelectorAll('button')].find(b => b.textContent.trim() === label)
const main = () => document.querySelector('.knowledge-main')
const visible = (text) => main()?.textContent.includes(text)
const assert = (ok, message) => { if (!ok) throw new Error(message) }
function enterText(control, value) {
  control.value = value
  control.dispatchEvent(new Event('input', {bubbles: true}))
}
async function until(predicate, message) {
  for (let attempt = 0; attempt < 80; attempt++) {
    const value = predicate()
    if (value) return value
    await new Promise(resolve => setTimeout(resolve, 20))
  }
  throw new Error(message)
}
function openHistory(title) {
  const item = [...document.querySelectorAll('.knowledge-history-list button')].find(b => b.textContent.includes(title))
  assert(item, '找不到历史条目 ' + title)
  item.click()
}
async function selectQuestion(title, expected) {
  openHistory(title)
  await until(() => visible(expected), '没有打开 ' + title)
}

async function exerciseWorkspace() {
  const enter = await until(() => button('发起知识问答'), '主页问答入口不可用')
  enter.click()
  await until(() => main() && button('提交提问') && document.querySelector('.knowledge-history-list'), '提问区未加载')
  const history = document.querySelector('.knowledge-history-list')
  assert(!history.textContent.includes('其他人的合成提问') && !history.textContent.includes('不得展示的受限题名'), '历史范围错误')
  const form = document.querySelector('.knowledge-form')
  button('什么是 USB PD？').click()
  form.querySelector('input[type=radio][value=false]').click()
  const external = form.querySelector('input[type=checkbox]')
  assert(external.checked, '员工默认外部处理选项不正确')
  const [sku, order] = form.querySelectorAll('.knowledge-fields input')
  enterText(sku, 'SYN-SKU')
  enterText(order, 'SYN-ORDER')
  await until(() => !button('提交提问').disabled, '提问按钮不可用')
  button('提交提问').click()
  await until(() => submitted && selectedStream, '提问未提交或未开启任务流')
  assert(submitted.question === '什么是 USB PD？' && !submitted.allow_public_reference && submitted.allow_external,
         '公开来源与外部处理授权没有独立提交')
  assert(submitted.sku_id === 'SYN-SKU' && submitted.order_id === 'SYN-ORDER', '业务编号未随首轮提交')
  selectedStream.dispatchEvent(new MessageEvent('result_delta', {data: JSON.stringify({id: 1, run_version: 1, text: '合成的流式段落'})}))
  await until(() => visible('合成的流式段落'), '流式段落未显示')
  await selectQuestion('合成内部问题', '内部资料答复')
  assert(selectedStream.closed && !visible('合成的流式段落'), '切换后仍显示上个任务的草稿')
  const groups = [...main().querySelectorAll('.knowledge-sources')]
  assert(groups.some(g => g.textContent.includes('答复引用') && g.textContent.includes('[2] 测试依据文件') && !g.textContent.includes('测试相关文件')), '正文引用编号错误')
  assert(groups.some(g => g.textContent.includes('相关资料') && g.textContent.includes('[1] 测试相关文件')), '候选文件未区分')
  main().querySelector('button[aria-label="展开引用 2"]').click()
  assert(main().querySelector('details[data-citation="2"]').open &&
         !main().querySelector('details[data-citation="1"]').open, '正文编号未展开对应原文')
  await selectQuestion('合成原文问题', '授权原文摘录')
  assert(button('补充条件再问'), '有待核实事项的原文不能补问')
  await selectQuestion('合成通用问题', '官方公开参考')
  const links = [...main().querySelectorAll('.knowledge-sources a')]
  assert(links.length === 1 && links[0].href === sources[0].url && links[0].rel.includes('noopener'), '公开出处链接异常')
  await selectQuestion('合成待补资料问题', '依据不足')
  assert(visible('请确认政策版本') && button('查看知识文档'), '缺少补充资料指引')
  button('补充条件再问').click()
  const retry = await until(() => button('提交为新任务'), '未回到补充条件编辑框')
  const edit = retry.closest('form')
  const question = edit.querySelector('textarea')
  assert(question.value === '合成待补资料问题', '未回填原问题')
  assert(edit.querySelector('input[type=radio][value=false]').checked && !edit.querySelector('input[type=checkbox]').checked,
         '新任务应仅用内部资料并关闭外部处理')
  assert([...edit.querySelectorAll('.knowledge-fields input')].every(input => !input.value), '业务编号跨任务残留')
  enterText(question, question.value + '，补充渠道为线下门店')
  retry.click()
  await until(() => visible('新增条件已提交'), '未展示补问的新任务')
  assert(submitted.question === '合成待补资料问题，补充渠道为线下门店' && !submitted.allow_public_reference && !submitted.allow_external,
         '补充条件或来源选择未提交')
  assert(submitted.sku_id === '' && submitted.order_id === '' && !submitted.conversation_id, '新任务继承了旧查询对象或会话')
  delayInternal = true
  openHistory('合成内部问题')
  await until(() => releaseOldDetail, '未发起旧任务的详情请求')
  await selectQuestion('合成待审问题', '等待负责人审核')
  releaseOldDetail()
  await new Promise(resolve => setTimeout(resolve, 40))
  assert(visible('等待负责人审核') && !visible('测试已核验正文'), '迟到的历史详情覆盖了当前任务')
  assert(!visible('不得展示的审核草稿') && !main().querySelector('.knowledge-answer'), '显示了未获授权的审核结果')
  await selectQuestion('受限提问', '当前无权阅读')
  assert(!visible('测试已核验正文') && !visible('不得展示的受限题名') && !button('补充条件再问'), '受限快照仍保留正文、原题或补问入口')
  document.body.dataset.knowledgeTest = 'passed'
}
window.addEventListener('load', () => exerciseWorkspace().catch(error => {
  const failure = document.createElement('pre')
  failure.textContent = String(error)
  document.body.append(failure)
}))
