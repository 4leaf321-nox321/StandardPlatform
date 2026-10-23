/**
 * 틀 편집 — 영역 키를 바꾸면 그 영역을 가리키던 짝 · 따라갈 영역도 옮긴다. 입력 중 잠깐 다른
 * 영역과 키가 겹쳤다가 벗어날 때는 옮기지 않는다 — 그 가리킴은 남은 영역의 것이다.
 */

import { describe, expect, it } from 'vitest'

import { blankArea } from '@/modules/slides/api'
import { withArea } from '@/modules/slides/SlideTemplateEditPage'

const asis = blankArea('asis', 0, 0)
const tobe = { ...blankArea('tobe', 0, 1), pair_of: 'asis' }
const risks = { ...blankArea('risks', 1, 0), follow_area: 'asis' }

describe('영역 키 바꾸기', () => {
  it('가리키던 짝 · 따라갈 영역이 새 키로 옮겨 간다', () => {
    const areas = withArea([asis, tobe, risks], 0, { ...asis, key: 'before' })
    expect(areas.map((one) => one.key)).toEqual(['before', 'tobe', 'risks'])
    expect(areas[1].pair_of).toBe('before')
    expect(areas[2].follow_area).toBe('before')
  })

  it('잠깐 겹친 키에서 벗어나면 남은 영역의 가리킴은 그대로다', () => {
    // 셋째 영역의 키를 치다가 「asis」 가 된 다음, 한 글자 더 쳐서 「asis2」 로.
    const typing = { ...blankArea('asis', 2, 0) }
    const areas = withArea([asis, tobe, risks, typing], 3, { ...typing, key: 'asis2' })
    expect(areas[1].pair_of).toBe('asis')
    expect(areas[2].follow_area).toBe('asis')
  })
})
