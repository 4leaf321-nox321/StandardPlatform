/**
 * 그래프의 색 — **같은 것은 어디서나 같은 색.**
 *
 * 화면마다 색을 고르면 같은 「부품」 이 구조 그림에서는 파랑이고 탐색 그림에서는 초록이 된다.
 *
 * ## 색은 타입이 아니라 **묶음**이 정한다
 *
 * 색을 타입 순서로 줬더니 팔레트가 열둘이라 **열셋째부터 전부 회색**이었다. 타입이 백 개가
 * 되는 설치에서는 거의 다 회색이다(실측).
 *
 * 색 백 개를 만드는 것은 답이 아니다 — 사람은 그만큼을 구별하지 못한다(열둘도 많은 편이다).
 * 그래서 색의 **뜻을 바꿨다**: 「이 타입」 이 아니라 **「어느 영역의 것인가」**. 그 영역이
 * 묶음이고, 어느 타입인지는 **라벨**이 말한다. 그림을 봤을 때 「파란 덩어리는 기준정보, 주황은
 * 시뮬레이션」 이 먼저 읽히는 것이 백 개를 각자 다른 색으로 칠하는 것보다 쓸모 있다.
 *
 *     상위 묶음   팔레트에서 색 하나(또는 묶음이 적어 둔 `color`)
 *     그 아래 묶음 같은 색의 **농도 차이** — 같은 계열로 보여야 한 영역으로 읽힌다
 *     타입        제 묶음의 색. 같은 묶음의 타입끼리는 아주 옅은 농도 차이만
 *     묶음 없음   회색 — 「아직 어디에도 안 넣은 것」 이라는 **맞는 뜻**이다
 */

/** 라이트·다크 둘 다에서 서로 구별되는 열두 색(Tailwind 500 계열). */
const PALETTE = [
  '#6366f1', // indigo
  '#10b981', // emerald
  '#f59e0b', // amber
  '#f43f5e', // rose
  '#0ea5e9', // sky
  '#8b5cf6', // violet
  '#14b8a6', // teal
  '#f97316', // orange
  '#84cc16', // lime
  '#d946ef', // fuchsia
  '#06b6d4', // cyan
  '#ef4444', // red
]

/**
 * 색이 없을 때의 회색.
 *
 * 묶음에 안 걸린 타입 · 열셋째 묶음부터가 이 색이다. 묶음이 열둘을 넘으면 **상위 묶음으로 한
 * 단계 더 묶는다** — 색을 더 만드는 것은 구별이 안 되는 색을 만드는 것이다.
 */
export const OVERFLOW_COLOR = '#9ca3af'

/** 같은 묶음 안에서 이웃을 가르는 농도 — 계열은 유지하고 밝기만 민다. */
const GROUP_STEP = 0.16
const TYPE_STEP = 0.07
/** 농도 단계는 돌려 쓴다 — 한 묶음에 타입이 스물이면 끝없이 밝아질 수 없다. */
const STEPS = [0, 1, -1, 2, -2]

export interface ColorGroup {
  slug: string
  /** 비우면 순서대로 팔레트에서 받는다. */
  color?: string | null
  /** 상위 묶음의 slug — 있으면 그 색을 물려받고 농도만 달라진다. */
  parent_slug?: string | null
}

export interface ColorType {
  slug: string
  nav_group_slug?: string | null
}

/** `#rrggbb` → `rgba()`. 외곽선·채움의 투명도를 따로 주려고 색 자체에 알파를 싣는다. */
export function withAlpha(hex: string, alpha: number): string {
  const h = hex.replace('#', '')
  const r = parseInt(h.slice(0, 2), 16)
  const g = parseInt(h.slice(2, 4), 16)
  const b = parseInt(h.slice(4, 6), 16)
  return `rgba(${r},${g},${b},${alpha})`
}

/**
 * 밝기를 민다 — `amount > 0` 이면 흰쪽으로, 음수면 검은쪽으로.
 *
 * 색조(hue)는 건드리지 않는다: 같은 묶음의 것들이 **같은 계열**로 보여야 한 영역으로 읽힌다.
 */
export function shade(hex: string, amount: number): string {
  if (!amount) return hex
  const h = hex.replace('#', '')
  const mix = (channel: number) =>
    amount > 0
      ? Math.round(channel + (255 - channel) * amount)
      : Math.round(channel * (1 + amount))
  const parts = [0, 2, 4].map((at) => mix(parseInt(h.slice(at, at + 2), 16)))
  return `#${parts.map((one) => Math.max(0, Math.min(255, one)).toString(16).padStart(2, '0')).join('')}`
}

/**
 * 묶음 → 색. **상위 묶음이 색을 쥐고, 그 아래는 농도로 갈린다.**
 *
 * 순서는 받은 차례 그대로다(서버가 `sort_order` 로 세운다) — 해시로 정하면 묶음이 셋뿐인데
 * 셋이 비슷한 색을 뽑는 날이 온다.
 */
export function groupColorScale(groups: ColorGroup[]): (slug: string | null | undefined) => string {
  const by = new Map(groups.map((one) => [one.slug, one]))
  /** 맨 위 묶음들 — 상위가 없는 것(또는 그 상위가 목록에 없는 것). */
  const tops = groups.filter((one) => !one.parent_slug || !by.has(one.parent_slug))
  const topAt = new Map(tops.map((one, at) => [one.slug, at]))

  const baseOf = (group: ColorGroup): string => {
    if (group.color) return group.color
    const at = topAt.get(group.slug)
    return at === undefined || at >= PALETTE.length ? OVERFLOW_COLOR : PALETTE[at]
  }

  const out = new Map<string, string>()
  for (const top of tops) {
    const base = baseOf(top)
    out.set(top.slug, base)
    // 같은 상위 밑의 묶음들 — 받은 차례대로 농도를 민다.
    const kids = groups.filter((one) => one.parent_slug === top.slug)
    kids.forEach((kid, at) => {
      out.set(
        kid.slug,
        kid.color ? kid.color : shade(base, STEPS[(at + 1) % STEPS.length] * GROUP_STEP),
      )
    })
  }
  return (slug) => (slug && out.get(slug)) || OVERFLOW_COLOR
}

/**
 * 타입 → 색. **제 묶음의 색**이고, 같은 묶음의 이웃끼리는 아주 옅은 농도 차이만 둔다.
 *
 * 묶음에 안 걸린 타입은 회색이다 — 그 회색은 「아직 어디에도 안 넣은 것」 이라는 뜻이고,
 * 「객체 타입 전부」 화면의 「묶음에 없는 타입」 구획과 같은 사실을 말한다.
 */
export function typeColorScale(
  types: ColorType[],
  groups: ColorGroup[],
): (slug: string | null | undefined) => string {
  const groupColor = groupColorScale(groups)
  const seen = new Map<string, number>()
  const out = new Map<string, string>()
  for (const type of types) {
    const group = type.nav_group_slug
    if (!group) {
      out.set(type.slug, OVERFLOW_COLOR)
      continue
    }
    const base = groupColor(group)
    const at = seen.get(group) ?? 0
    seen.set(group, at + 1)
    out.set(
      type.slug,
      base === OVERFLOW_COLOR ? base : shade(base, STEPS[at % STEPS.length] * TYPE_STEP),
    )
  }
  return (slug) => (slug && out.get(slug)) || OVERFLOW_COLOR
}

/**
 * 순서대로 색 — **묶음이 없는 것들**에 쓴다(커뮤니티 덩어리처럼 데이터에서 나온 묶음).
 *
 * 타입의 색에는 쓰지 않는다: 그 길로 가면 열셋째부터 회색이 된다.
 */
export function colorScale(keys: string[]): (key: string) => string {
  const index = new Map(keys.map((key, i) => [key, i]))
  return (key) => {
    const i = index.get(key)
    if (i === undefined || i >= PALETTE.length) return OVERFLOW_COLOR
    return PALETTE[i]
  }
}
