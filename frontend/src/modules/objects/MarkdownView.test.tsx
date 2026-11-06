/**
 * 마크다운 보기의 제목 id(ADR 0053 · 0054) — 서버의 `slug_of` 와 같은 규칙이고, **몇 번을 그려도 같다.**
 *
 * 개발 화면(StrictMode)은 컴포넌트를 두 번 그린다. 제목 컴포넌트 안에서 같은 제목을 세던 때는 모든
 * id 에 `-2` 가 붙어, 뜻으로 찾기 · 문서 속 링크의 `#4-2-결과` 가 그 절을 못 찾았다(2026-11-06).
 */

import { render } from '@testing-library/react'
import { StrictMode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import { MarkdownView } from '@/modules/objects/MarkdownView'

const BODY = `# 백서

## 4.2 결과

### **굵은** [링크](/o/part/x) \`코드\`

## 4.2 결과

#### 넷째 단
`

function view(body: string) {
  return (
    <StrictMode>
      <MemoryRouter>
        <MarkdownView body={body} />
      </MemoryRouter>
    </StrictMode>
  )
}

describe('마크다운 보기의 제목 id', () => {
  it('두 번 그려도 밀리지 않는다 — 같은 제목만 -2, h4 는 id 없음', () => {
    const { container, rerender } = render(view(BODY))
    const ids = () => Array.from(container.querySelectorAll('h1, h2, h3, h4')).map((one) => one.id)
    const want = ['백서', '4-2-결과', '굵은-링크-코드', '4-2-결과-2', '']
    expect(ids()).toEqual(want)

    rerender(view(BODY))
    expect(ids()).toEqual(want)
  })
})
