/**
 * 마크다운 보기(ADR 0053) — 문서 속성의 본문을 그린다. 제목 · 목록 · 표(GFM) · 인용 · 코드.
 *
 * - **글은 HTML 로 해석하지 않는다**(react-markdown 의 기본) — 본문에 든 `<script>` 는 글자로 선다.
 *   AI 가 넣은 본문도 같다.
 * - **안쪽 링크(`/o/…`)는 화면 안에서 옮긴다** — 새로 고치지 않는다. 바깥 링크는 새 탭.
 * - **그림은 아직 그리지 않는다** — 폐쇄망에서 바깥 주소의 그림은 깨져 보이고, 첨부를 가리키는 길은
 *   다음 판이다. 자리에 캡션을 남긴다.
 * - 제목 h1 ~ h3 마다 id 를 단다 — 목차가 그리로 옮기고, 뜻으로 찾기 · 문서 속 링크의 `#<제목 id>` 가
 *   그 절로 간다(같은 제목이 또 나오면 `-2`). 서버의 `semantic.services.slug_of` 와 같은 규칙.
 */

import Markdown from 'react-markdown'
import type { Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Link } from 'react-router-dom'

/** 본문을 푼 나무(hast)의 마디 — 여기서 쓰는 만큼만. */
interface TreeNode {
  type: string
  tagName?: string
  value?: string
  properties?: Record<string, unknown>
  children?: TreeNode[]
}

/** 제목의 글자 — 굵게 · 링크 · 코드가 섞인 제목도 글자만. */
function textOf(node: TreeNode): string {
  if (node.type === 'text') return node.value ?? ''
  return (node.children ?? []).map(textOf).join('')
}

/**
 * 제목 id 는 **본문을 풀 때 한 번** 단다. 그리는 쪽(제목 컴포넌트)에서 세면 React 가 같은
 * 컴포넌트를 두 번 그릴 때마다(개발의 StrictMode · 다시 그리기) 셈이 밀려 모든 id 에 `-2` 가 붙고,
 * `#4-2-결과` 로 들어와도 그 절을 못 찾았다(2026-11-06).
 */
function rehypeHeadingIds() {
  return (tree: TreeNode) => {
    const used = new Map<string, number>()
    const walk = (node: TreeNode) => {
      if (node.type === 'element' && /^h[1-3]$/.test(node.tagName ?? '')) {
        const slug = slugOf(textOf(node))
        const count = (used.get(slug) ?? 0) + 1
        used.set(slug, count)
        node.properties = { ...node.properties, id: count === 1 ? slug : `${slug}-${count}` }
        return
      }
      node.children?.forEach(walk)
    }
    walk(tree)
  }
}

function slugOf(text: string): string {
  return (
    text
      .trim()
      .toLowerCase()
      .replace(/[^\p{L}\p{N}]+/gu, '-')
      .replace(/^-+|-+$/g, '') || 'section'
  )
}

const COMPONENTS: Components = {
  h1: ({ id, children }) => (
    <h1 id={id} className="mt-6 mb-3 scroll-mt-4 text-xl font-semibold first:mt-0">
      {children}
    </h1>
  ),
  h2: ({ id, children }) => (
    <h2 id={id} className="mt-6 mb-2 scroll-mt-4 text-lg font-semibold first:mt-0">
      {children}
    </h2>
  ),
  h3: ({ id, children }) => (
    <h3 id={id} className="mt-4 mb-2 scroll-mt-4 font-semibold first:mt-0">
      {children}
    </h3>
  ),
  h4: ({ children }) => <h4 className="mt-3 mb-1 text-sm font-semibold">{children}</h4>,
  p: ({ children }) => <p className="my-2 leading-7">{children}</p>,
  ul: ({ children }) => <ul className="my-2 list-disc space-y-1 pl-6">{children}</ul>,
  ol: ({ children }) => <ol className="my-2 list-decimal space-y-1 pl-6">{children}</ol>,
  blockquote: ({ children }) => (
    <blockquote className="text-muted-foreground my-3 border-l-2 pl-3">{children}</blockquote>
  ),
  hr: () => <hr className="my-4" />,
  table: ({ children }) => (
    <div className="my-3 overflow-x-auto rounded-md border">
      <table className="w-full text-sm">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-muted/50">{children}</thead>,
  th: ({ children }) => (
    <th className="border-b px-3 py-1.5 text-left font-medium whitespace-nowrap">{children}</th>
  ),
  td: ({ children }) => <td className="border-b px-3 py-1.5 align-top">{children}</td>,
  pre: ({ children }) => (
    <pre className="bg-muted my-3 overflow-x-auto rounded-md p-3 text-xs [&_code]:bg-transparent [&_code]:p-0">
      {children}
    </pre>
  ),
  code: ({ children }) => (
    <code className="bg-muted rounded px-1 py-0.5 font-mono text-[0.85em]">{children}</code>
  ),
  a: ({ href, children }) =>
    href && href.startsWith('/') && !href.startsWith('//') ? (
      <Link
        to={href}
        className="decoration-muted-foreground/50 hover:decoration-foreground underline underline-offset-4"
      >
        {children}
      </Link>
    ) : (
      <a
        href={href}
        target="_blank"
        rel="noreferrer"
        className="decoration-muted-foreground/50 hover:decoration-foreground underline underline-offset-4"
      >
        {children}
      </a>
    ),
  img: ({ alt }) => (
    <span className="text-muted-foreground rounded border border-dashed px-1.5 py-0.5 text-xs">
      그림{alt ? `: ${alt}` : ''}
    </span>
  ),
}

export function MarkdownView({ body }: { body: string }) {
  return (
    <div className="text-sm break-words">
      <Markdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHeadingIds]}
        components={COMPONENTS}
      >
        {body}
      </Markdown>
    </div>
  )
}
