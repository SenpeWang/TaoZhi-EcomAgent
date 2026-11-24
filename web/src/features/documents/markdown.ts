import { marked } from 'marked'

/** 允许在文档内保留的链接协议；其余协议会在渲染后剔除。 */
const SAFE_HREF = /^(?:https?:|mailto:|#|\/(?!\/))/

/**
 * 把 Markdown 原文渲染为可安全插入页面的 HTML。
 *
 * 先转义源文本中的 ``<`` 与 ``&``，阻止文档内嵌原始 HTML 或脚本执行；
 * 再移除图片与非 http(s)/mailto/站内链接，避免文档触发外连或脚本协议。
 *
 * @param source - 文档正文中的 Markdown 源文本。
 * @returns 已清洗的 HTML 字符串；空输入返回空串。
 */
export function renderMarkdown(source: string): string {
  if (!source) return ''
  const escaped = source.replace(/&/g, '&amp;').replace(/</g, '&lt;')
  let html = marked.parse(escaped, { async: false, gfm: true })
  html = html.replace(/<img\b[^>]*>/gi, '')
  html = html.replace(/\shref="([^"]*)"/gi, (match, href: string) =>
    SAFE_HREF.test(href) ? match : '',
  )
  html = html.replace(
    /<a href="(https?:\/\/[^"]*)"/g,
    '<a href="$1" target="_blank" rel="noopener noreferrer"',
  )
  return html
}
