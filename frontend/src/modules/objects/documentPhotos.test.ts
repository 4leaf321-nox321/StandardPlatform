/**
 * 문서의 그림(ADR 0056) — 커서 자리에 표시를 넣고, 몇 곳에서 쓰는지 센다. 표기는 자주검증과 같다.
 */

import { describe, expect, it } from 'vitest'

import { photoIdsIn, photoSource } from '@/modules/files/photoMarker'
import { insertPhotos, usesOf } from '@/modules/objects/documentPhotos'

const ONE = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'
const TWO = '11111111-2222-4333-8444-555555555555'

describe('문서의 그림 표시', () => {
  it('그림 하나는 제 문단 하나 — 줄 중간이면 가르고, 이미 띄운 빈 줄은 더하지 않는다', () => {
    expect(insertPhotos('앞뒤', 1, [ONE]).text).toBe(`앞\n\n![](photo:${ONE})\n\n뒤`)
    expect(insertPhotos('앞\n\n뒤', 3, [ONE]).text).toBe(`앞\n\n![](photo:${ONE})\n\n뒤`)
    expect(insertPhotos('', 0, [ONE, TWO]).text).toBe(`![](photo:${ONE})\n\n![](photo:${TWO})\n`)
    const end = insertPhotos('글', 1, [ONE])
    expect(end.text).toBe(`글\n\n![](photo:${ONE})\n`)
    expect(end.caret).toBe(end.text.length)
  })

  it('주소에서 첨부 id 와 폭을 읽고, 폭을 안 적었으면 그림 크기대로', () => {
    expect(photoSource(`photo:${ONE.toUpperCase()}?w=180`)).toEqual({ id: ONE, width: 100 })
    expect(photoSource(`photo:${ONE}`)).toEqual({ id: ONE, width: null })
    expect(photoSource('https://example.com/a.png')).toBeNull()
  })

  it('몇 곳에서 쓰나는 글 사이의 것까지 센다', () => {
    const body = `![가](photo:${ONE}?w=50)\n\n글 ![나](photo:${ONE}) 글\n\n![](photo:${TWO})`
    expect(usesOf(body, ONE)).toBe(2)
    expect(photoIdsIn(body)).toEqual([ONE, TWO])
  })
})
