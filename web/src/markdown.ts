/**
 * 轻量、无第三方依赖的 XSS 安全 Markdown 渲染与结构解析工具。
 * 先对输入执行完整 HTML 实体转义，再将标准 Markdown 语法编译为受控排版 HTML。
 */

export interface MarkdownHeading {
  id: string
  level: number
  text: string
}

interface RenderedMarkdown {
  html: string
  headings: MarkdownHeading[]
}

/**
 * 对原始文本执行严格 HTML 实体转义，阻断脚本注入。
 * @param text - 原始输入文本。
 * @returns 转义后的纯安全文本。
 */
function escapeHtml(text: string): string {
  return text
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;')
}

/**
 * 校验并清洗外链地址，仅放行安全的 http / https 协议链接。
 * @param rawUrl - 原始链接字符串（可能含 HTML 转义实体）。
 * @returns 安全外链 URL；不合规时返回空字符串。
 */
function sanitizeHref(rawUrl: string): string {
  const unescaped = rawUrl.replaceAll('&amp;', '&').trim()
  if (!/^https?:\/\/[^\s<>"']+$/i.test(unescaped)) {
    return ''
  }
  return escapeHtml(unescaped)
}

/**
 * 渲染行内 Markdown 语法（行内代码、链接、加粗、斜体、删除线）。
 * @param rawLine - 未转义的单行文本。
 * @param allowTableBr - 是否将常见表格内换行符转换为安全换行标签。
 * @returns 编译后的安全行内 HTML。
 */
function renderInline(rawLine: string, allowTableBr = false): string {
  const escaped = escapeHtml(rawLine)
  const codeTokens: string[] = []
  const rawCodeTokens: string[] = []

  // 先提取行内代码，避免内部星号或 URL 被二次解析
  let work = escaped.replace(/`([^`\n]+)`/g, (_match, code: string) => {
    const idx = codeTokens.length
    rawCodeTokens.push(code)
    codeTokens.push(`<code class="md-inline-code">${code}</code>`)
    return `\uE000CODE${idx}\uE001`
  })

  if (allowTableBr) {
    work = work.replace(/&lt;br\s*\/?&gt;/gi, '<br />')
  }

  /**
   * 还原链接地址内的代码占位符为纯文本，防止后续渲染成 HTML 标签混入 href 属性。
   * @param text - 含占位符的原始链接文本。
   * @returns 还原后的纯文本 URL。
   */
  const unwrapCodePlaceholders = (text: string): string =>
    text.replace(/\uE000CODE(\d+)\uE001/g, (_m, idx: string) => rawCodeTokens[Number(idx)] ?? '')

  // 解析 [文本](url) 链接并占位保护，防止外层再次匹配裸 URL
  const linkTokens: string[] = []
  work = work.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_match, label: string, url: string) => {
    const safeUrl = sanitizeHref(unwrapCodePlaceholders(url))
    const idx = linkTokens.length
    if (!safeUrl) {
      linkTokens.push(label)
    } else {
      linkTokens.push(
        `<a class="md-link" href="${safeUrl}" target="_blank" rel="noopener noreferrer">${label}</a>`,
      )
    }
    return `\uE000LINK${idx}\uE001`
  })

  // 自动识别裸露的 http/https 链接，剥离尾部常见中英文标点
  work = work.replace(
    /(https?:\/\/[^\s<>"'\u3002\uff0c\uff1b\uff1a\u3001\uff09\u3011\uff1e]+)/g,
    (fullUrl: string) => {
      const cleaned = fullUrl.replace(/[.,;:!?)]+$/, '')
      const suffix = fullUrl.slice(cleaned.length)
      const safeUrl = sanitizeHref(unwrapCodePlaceholders(cleaned))
      if (!safeUrl) {
        return fullUrl
      }
      const idx = linkTokens.length
      linkTokens.push(
        `<a class="md-link" href="${safeUrl}" target="_blank" rel="noopener noreferrer">${safeUrl}</a>`,
      )
      return `\uE000LINK${idx}\uE001${suffix}`
    },
  )

  // 加粗、删除线与斜体
  work = work.replace(/\*\*([^*\n]+(?:\*[^*\n]+)*)\*\*/g, '<strong>$1</strong>')
  work = work.replace(/__([^_\n]+(?:_[^_\n]+)*)__/g, '<strong>$1</strong>')
  work = work.replace(/~~([^~\n]+)~~/g, '<del>$1</del>')
  work = work.replace(/(^|[\s(>：:])\*([^*\n]+)\*(?=$|[\s).,;!?，。；：、）])/g, '$1<em>$2</em>')

  // 还原链接与代码占位符
  work = work.replace(/\uE000LINK(\d+)\uE001/g, (_m, idx: string) => linkTokens[Number(idx)] ?? '')
  work = work.replace(/\uE000CODE(\d+)\uE001/g, (_m, idx: string) => codeTokens[Number(idx)] ?? '')
  return work
}

/**
 * 切分 Markdown 表格行的单元格，支持反斜杠转义竖线。
 * @param line - 表格单行文本。
 * @returns 清理首尾空白后的单元格文本数组。
 */
function splitTableCells(line: string): string[] {
  let trimmed = line.trim()
  if (trimmed.startsWith('|')) {
    trimmed = trimmed.slice(1)
  }
  if (trimmed.endsWith('|') && !trimmed.endsWith('\\|')) {
    trimmed = trimmed.slice(0, -1)
  }
  const cells: string[] = []
  let current = ''
  for (let i = 0; i < trimmed.length; i += 1) {
    const ch = trimmed[i]
    if (ch === '\\' && trimmed[i + 1] === '|') {
      current += '|'
      i += 1
    } else if (ch === '|') {
      cells.push(current.trim())
      current = ''
    } else {
      current += ch
    }
  }
  cells.push(current.trim())
  return cells
}

/**
 * 解析 Markdown 表格分隔线行，返回各列对齐方式。
 * @param line - 分隔线候选行。
 * @returns 列对齐方式列表；若不是合法分隔线则返回 null。
 */
function parseTableAlignments(line: string): Array<'left' | 'center' | 'right'> | null {
  if (!line.includes('|')) {
    return null
  }
  const rawCells = splitTableCells(line)
  if (rawCells.length === 0) {
    return null
  }
  const aligns: Array<'left' | 'center' | 'right'> = []
  for (const cell of rawCells) {
    const token = cell.trim()
    if (!/^:?-{2,}:?$/.test(token)) {
      return null
    }
    if (token.startsWith(':') && token.endsWith(':')) {
      aligns.push('center')
    } else if (token.endsWith(':')) {
      aligns.push('right')
    } else {
      aligns.push('left')
    }
  }
  return aligns
}

/**
 * 判断一行是否为分割线（--- / *** / ___）。
 * @param line - 原始单行。
 * @returns 是否为水平分割线。
 */
function isHorizontalRule(line: string): boolean {
  return /^(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line.trim())
}

/**
 * 识别引用提示块的语义色调，便于人眼区分风险缺口与常规提示。
 * @param quoteText - 引用块合并后的纯文本。
 * @returns 样式修饰类名。
 */
function classifyCalloutTone(quoteText: string): string {
  const head = quoteText.trim().slice(0, 40)
  if (/^(?:⚠️|注：|注意|风险|警告|缺口|缺失|数据缺口|降级|异常)/.test(head)) {
    return 'md-callout--warn'
  }
  if (/^(?:💡|✅|核心结论|总结|结论|建议|提示)/.test(head)) {
    return 'md-callout--info'
  }
  return 'md-callout--neutral'
}

interface ParsedListItem {
  indent: number
  ordered: boolean
  text: string
}

/**
 * 尝试解析列表项（无序列表前缀或有序数字列表）。
 * @param line - 原始单行文本。
 * @returns 列表项结构，非列表行返回 null。
 */
function matchListItem(line: string): ParsedListItem | null {
  const unordered = line.match(/^(\s*)[-*+]\s+(.*)$/)
  if (unordered) {
    return {
      indent: unordered[1].replaceAll('\t', '  ').length,
      ordered: false,
      text: unordered[2],
    }
  }
  const ordered = line.match(/^(\s*)\d+[.)]\s+(.*)$/)
  if (ordered) {
    return {
      indent: ordered[1].replaceAll('\t', '  ').length,
      ordered: true,
      text: ordered[2],
    }
  }
  return null
}

/**
 * 将列表行渲染为嵌套有序/无序 HTML 列表。
 * @param lines - 连续列表块的所有原始行。
 * @returns 编译后的列表 HTML 字符串。
 */
function renderListBlock(lines: string[]): string {
  interface StackEntry {
    tag: 'ul' | 'ol'
    indent: number
    hasOpenLi: boolean
  }
  const stack: StackEntry[] = []
  const out: string[] = []

  for (const line of lines) {
    const item = matchListItem(line)
    if (!item) {
      continue
    }
    const desiredTag: 'ul' | 'ol' = item.ordered ? 'ol' : 'ul'

    while (stack.length > 0 && item.indent < stack[stack.length - 1].indent) {
      const closed = stack.pop()
      if (closed) {
        if (closed.hasOpenLi) {
          out.push('</li>')
        }
        out.push(`</${closed.tag}>`)
      }
    }

    if (
      stack.length > 0 &&
      item.indent === stack[stack.length - 1].indent &&
      stack[stack.length - 1].tag !== desiredTag
    ) {
      const switched = stack.pop()
      if (switched) {
        if (switched.hasOpenLi) {
          out.push('</li>')
        }
        out.push(`</${switched.tag}>`)
      }
    }

    if (stack.length === 0 || item.indent > stack[stack.length - 1].indent) {
      const listCls = desiredTag === 'ol' ? 'md-ol' : 'md-ul'
      out.push(`<${desiredTag} class="${listCls}">`)
      stack.push({ tag: desiredTag, indent: item.indent, hasOpenLi: false })
    }

    const top = stack[stack.length - 1]
    if (top.hasOpenLi) {
      out.push('</li>')
    }

    // 支持任务复选框 [ ] / [x]
    let bodyHtml = ''
    const todoMatch = item.text.match(/^\[([ xX])\]\s+(.*)$/)
    if (todoMatch) {
      const checked = todoMatch[1].toLowerCase() === 'x'
      const badge = checked
        ? '<span class="md-todo md-todo--done">✓</span>'
        : '<span class="md-todo"></span>'
      bodyHtml = `${badge}<span>${renderInline(todoMatch[2])}</span>`
      out.push(`<li class="md-li md-li--todo">${bodyHtml}`)
    } else {
      bodyHtml = renderInline(item.text)
      out.push(`<li class="md-li">${bodyHtml}`)
    }
    top.hasOpenLi = true
  }

  while (stack.length > 0) {
    const closed = stack.pop()
    if (closed) {
      if (closed.hasOpenLi) {
        out.push('</li>')
      }
      out.push(`</${closed.tag}>`)
    }
  }
  return out.join('')
}

/**
 * 将 Markdown 文本编译为人类友好的结构化 HTML，并提取目录章节节点。
 * @param source - 原始 Markdown 文本。
 * @param idPrefix - 锚点前缀，避免同一页面多轮回答 ID 冲突。
 * @returns 含安全 HTML 与章节目录的结构化渲染结果。
 */
export function renderMarkdownReport(source: string, idPrefix = 'md-sec'): RenderedMarkdown {
  const normalized = (source || '').replaceAll('\r\n', '\n').trim()
  if (!normalized) {
    return { html: '', headings: [] }
  }

  const lines = normalized.split('\n')
  const blocks: string[] = []
  const headings: MarkdownHeading[] = []
  let i = 0

  while (i < lines.length) {
    const line = lines[i]
    const trimmed = line.trim()

    if (!trimmed) {
      i += 1
      continue
    }

    // 1. 围栏代码块 ```
    const fenceMatch = trimmed.match(/^```([A-Za-z0-9_-]*)\s*$/)
    if (fenceMatch) {
      const lang = fenceMatch[1]
      const codeLines: string[] = []
      i += 1
      while (i < lines.length && !lines[i].trim().startsWith('```')) {
        codeLines.push(lines[i])
        i += 1
      }
      if (i < lines.length && lines[i].trim().startsWith('```')) {
        i += 1
      }
      const langBadge = lang ? `<div class="md-code-lang">${escapeHtml(lang)}</div>` : ''
      blocks.push(
        `<pre class="md-code-block">${langBadge}<code>${escapeHtml(codeLines.join('\n'))}</code></pre>`,
      )
      continue
    }

    // 2. 分割线
    if (isHorizontalRule(line)) {
      blocks.push('<hr class="md-divider" />')
      i += 1
      continue
    }

    // 3. 标题 # ~ ######
    const headingMatch = line.match(/^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$/)
    if (headingMatch) {
      const rawLevel = headingMatch[1].length
      const level = Math.min(rawLevel, 4)
      const text = headingMatch[2].trim()
      const anchorId = `${idPrefix}-${headings.length}`
      headings.push({
        id: anchorId,
        level: rawLevel,
        text: text.replace(/[*_`~]/g, ''),
      })
      blocks.push(
        `<h${level} id="${escapeHtml(anchorId)}" class="md-h md-h${level}">${renderInline(text)}</h${level}>`,
      )
      i += 1
      continue
    }

    // 4. GFM 表格（表头行 + 分隔行）
    if (i + 1 < lines.length && line.includes('|')) {
      const aligns = parseTableAlignments(lines[i + 1])
      if (aligns) {
        const headers = splitTableCells(line)
        const colCount = Math.max(headers.length, aligns.length)
        const theadCells: string[] = []
        for (let c = 0; c < colCount; c += 1) {
          const align = aligns[c] ?? 'left'
          theadCells.push(
            `<th style="text-align:${align}">${renderInline(headers[c] ?? '', true)}</th>`,
          )
        }

        const bodyRows: string[] = []
        i += 2
        while (i < lines.length) {
          const rowLine = lines[i]
          if (!rowLine.trim() || !rowLine.includes('|') || isHorizontalRule(rowLine)) {
            break
          }
          const cells = splitTableCells(rowLine)
          const tds: string[] = []
          for (let c = 0; c < colCount; c += 1) {
            const align = aligns[c] ?? 'left'
            tds.push(`<td style="text-align:${align}">${renderInline(cells[c] ?? '', true)}</td>`)
          }
          bodyRows.push(`<tr>${tds.join('')}</tr>`)
          i += 1
        }
        blocks.push(
          `<div class="md-table-wrap"><table class="md-table"><thead><tr>${theadCells.join('')}</tr></thead><tbody>${bodyRows.join('')}</tbody></table></div>`,
        )
        continue
      }
    }

    // 5. 引用 / 提示块 >
    if (/^\s*>\s?/.test(line)) {
      const quoteLines: string[] = []
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        quoteLines.push(lines[i].replace(/^\s*>\s?/, ''))
        i += 1
      }
      const rawQuote = quoteLines.join('\n').trim()
      const toneClass = classifyCalloutTone(rawQuote)
      const innerHtml = quoteLines
        .map((ql) => renderInline(ql.trim()))
        .filter((item) => item.length > 0)
        .join('<br />')
      blocks.push(`<blockquote class="md-callout ${toneClass}">${innerHtml}</blockquote>`)
      continue
    }

    // 6. 有序 / 无序列表
    if (matchListItem(line)) {
      const listLines: string[] = []
      while (i < lines.length) {
        const cur = lines[i]
        if (matchListItem(cur)) {
          listLines.push(cur)
          i += 1
        } else if (!cur.trim() && i + 1 < lines.length && matchListItem(lines[i + 1])) {
          // 列表项之间允许单个空行继续同一列表
          i += 1
        } else {
          break
        }
      }
      blocks.push(renderListBlock(listLines))
      continue
    }

    // 7. 普通段落（遇到下一块级语法起始行时中止合并）
    const paraLines: string[] = []
    while (i < lines.length) {
      const cur = lines[i]
      const curTrim = cur.trim()
      if (
        !curTrim ||
        /^```/.test(curTrim) ||
        isHorizontalRule(cur) ||
        /^\s{0,3}#{1,6}\s+/.test(cur) ||
        /^\s*>\s?/.test(cur) ||
        matchListItem(cur) ||
        (i + 1 < lines.length && cur.includes('|') && parseTableAlignments(lines[i + 1]))
      ) {
        break
      }
      paraLines.push(cur.trim())
      i += 1
    }
    if (paraLines.length > 0) {
      blocks.push(`<p class="md-p">${paraLines.map((pl) => renderInline(pl)).join('<br />')}</p>`)
    }
  }

  return {
    html: blocks.join(''),
    headings,
  }
}
