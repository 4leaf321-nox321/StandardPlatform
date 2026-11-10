/**
 * 마크다운 보기(ADR 0053) — 문서 속성의 본문을 그린다. 제목 · 목록 · 표(GFM) · 인용 · 코드.
 *
 * - **글은 HTML 로 해석하지 않는다**(react-markdown 의 기본) — 본문에 든 `<script>` 는 글자로 선다.
 *   AI 가 넣은 본문도 같다.
 * - **안쪽 링크(`/o/…`)는 화면 안에서 옮긴다** — 새로 고치지 않는다. 바깥 링크는 새 탭.
 * - **그림은 그 문서의 그림(첨부)만 그린다**(ADR 0056) — 본문의 `![캡션](photo:<첨부 id>?w=<폭>)`.
 *   폭은 본문 너비의 %(비우면 그림 크기대로, 본문보다 넓으면 맞춘다). 누르면 크게 본다.
 *   - 넘겨받은 `photos`(그 문서 자리의 첨부)에 있고 서버가 이미지로 읽은 것만 띄운다 — blob 주소는
 *     앱과 같은 출처라 HTML · SVG 를 띄우면 그 안의 스크립트가 앱의 권한으로 돈다(ADR 0012).
 *   - 없는 id(삭제한 그림 · 남의 문서 그림)는 캡션과 까닭을 적는다 — 옛 판도 그렇게 보인다.
 *   - 바깥 주소의 그림은 그리지 않는다 — 폐쇄망에서 깨져 보인다. 자리에 캡션을 남긴다.
 * - 제목 h1 ~ h3 마다 id 를 단다 — 목차가 그리로 옮기고, 뜻으로 찾기 · 문서 속 링크의 `#<제목 id>` 가
 *   그 절로 간다(같은 제목이 또 나오면 `-2`). 서버의 `semantic.services.slug_of` 와 같은 규칙.
 */

import { useMemo, useState } from 'react'
import Markdown, { defaultUrlTransform } from 'react-markdown'
import type { Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Link } from 'react-router-dom'

import { attachmentPaths } from '@/modules/files/api'
import type { Attachment } from '@/modules/files/api'
import { ImageViewer } from '@/modules/files/ImageViewer'
import { photoSource } from '@/modules/files/photoMarker'
import { useBlobUrl } from '@/shared/hooks/useBlobUrl'

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
}

/** 그리지 않는 그림의 자리 — 캡션과 (있으면) 까닭. */
function Caption({ alt, why }: { alt?: string; why?: string }) {
  return (
    <span className="text-muted-foreground rounded border border-dashed px-1.5 py-0.5 text-xs">
      그림{alt ? `: ${alt}` : ''}
      {why ? ` — ${why}` : ''}
    </span>
  )
}

/** 사진 표시의 그림 하나 — 폭(본문 너비의 %)이 작으면 미리보기(320px), 크면 원본을 받는다. */
function Figure({
  photo,
  width,
  alt,
  onOpen,
}: {
  photo: Attachment
  width: number | null
  alt: string
  onOpen: () => void
}) {
  // 본문 너비의 40% 를 넘게 그리면 미리보기(긴 변 320px)로는 흐리다 — 그때만 원본을 받는다.
  const thumb = width !== null && width <= 40
  const blob = useBlobUrl(
    thumb ? attachmentPaths.thumbnail(photo.id) : attachmentPaths.content(photo.id),
  )
  const name = alt || photo.original_name
  const ratio = photo.width && photo.height ? `${photo.width} / ${photo.height}` : undefined
  return (
    <span
      className="my-3 block max-w-full"
      style={{
        width: width !== null ? `${width}%` : undefined,
        // 폭을 안 적었으면 그림 크기대로 — 본문보다 넓으면 맞춘다.
        maxWidth: width === null && photo.width ? `min(100%, ${photo.width}px)` : undefined,
      }}
    >
      <button
        type="button"
        onClick={onOpen}
        title={`${name} 크게 보기`}
        aria-label={`${name} 크게 보기`}
        style={{ aspectRatio: ratio }}
        className="bg-muted/30 block w-full overflow-hidden rounded-sm border"
      >
        {blob.url ? (
          <img src={blob.url} alt={name} className="size-full object-contain" />
        ) : blob.error ? (
          <span className="text-muted-foreground block p-2 text-xs">
            그림을 받지 못했습니다 — {blob.error.message}
          </span>
        ) : null}
      </button>
      {alt && <span className="text-muted-foreground mt-1 block text-center text-xs">{alt}</span>}
    </span>
  )
}

/** `photo:` 는 사진 표시다 — 기본 거르기(`defaultUrlTransform`)는 모르는 주소를 지운다. */
function urlTransform(url: string): string {
  return url.startsWith('photo:') ? url : defaultUrlTransform(url)
}

/**
 * 마크다운 본문. `photos` 는 그 문서 자리의 첨부(`owner_field` 가 문서 키) — 주면 사진 표시를
 * 그림으로 그리고, 안 주면(그림을 모르는 자리) 캡션만 남긴다.
 */
export function MarkdownView({ body, photos }: { body: string; photos?: Attachment[] }) {
  const [open, setOpen] = useState<string | null>(null)
  const images = useMemo(() => (photos ?? []).filter((one) => one.is_image), [photos])
  const components = useMemo<Components>(
    () => ({
      ...COMPONENTS,
      img: ({ src, alt }) => {
        const source = typeof src === 'string' ? photoSource(src) : null
        if (!source || photos === undefined) return <Caption alt={alt} />
        const photo = images.find((one) => one.id.toLowerCase() === source.id)
        if (!photo) {
          return <Caption alt={alt} why="삭제됐거나 이 문서의 그림이 아닙니다" />
        }
        return (
          <Figure
            photo={photo}
            width={source.width}
            alt={alt ?? ''}
            onOpen={() => setOpen(photo.id)}
          />
        )
      },
    }),
    [images, photos],
  )
  const index = open ? images.findIndex((one) => one.id === open) : -1
  return (
    <div className="text-sm break-words">
      <Markdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHeadingIds]}
        components={components}
        urlTransform={urlTransform}
      >
        {body}
      </Markdown>
      {index >= 0 && (
        <ImageViewer
          images={images}
          index={index}
          onIndex={(next) => setOpen(images[next]?.id ?? null)}
          onClose={() => setOpen(null)}
        />
      )}
    </div>
  )
}
