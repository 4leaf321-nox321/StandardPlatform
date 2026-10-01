/**
 * 객체 타입 전부 — **백 개가 되면 사이드바는 색인이 못 된다.**
 *
 * 사이드바는 240px 짜리 한 줄 목록이라 검색·건수·설명을 둘 자리가 없고, 타입이 백 개면
 * 제목까지 백열 줄이 된다. 그래서 색인은 이 화면이 지고, 사이드바는 「자주 가는 길」 만 진다.
 *
 * ## 묶음별
 *
 *     상위 묶음 › 묶음 › 타입. **화면 정리**의 계층이다(`nav_groups.parent_id`)
 *
 * 「개발모델은 제품이다」 같은 **뜻**의 계층은 인터페이스가 말한다(ADR 0006) — 옛 상위 타입
 * (`parent_slug`)은 없어졌다.
 *
 * ## 인터페이스
 *
 *     인터페이스 › 구현 타입. 줄을 누르면 구현 타입 전부를 한 목록으로 본다(`/o/<인터페이스>`).
 *     인터페이스가 하나도 없으면 탭을 안 그린다 — 빈 탭은 「무언가 빠졌다」 로 읽힌다.
 *
 * **묶음에 안 걸린 타입도 여기 나온다.** 사이드바는 그것을 아예 안 그리므로(묶음이 없으면
 * 걸 자리가 없다), 만들어 놓고 묶음을 안 정한 타입은 주소를 직접 쳐야만 갈 수 있었다.
 */

import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Search } from 'lucide-react'

import { ontologyApi } from '@/modules/ontology/api'
import type { ObjectInterface, ObjectType } from '@/modules/ontology/api'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { Badge } from '@/shared/components/ui/badge'
import { Input } from '@/shared/components/ui/input'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/shared/components/ui/tabs'
import { useResource } from '@/shared/hooks/useResource'
import { iconOf } from '@/shared/icons'
import { cn } from '@/shared/lib/utils'

/** 찾기는 **이름 · 식별자 · 설명**을 본다 — 타입 이름을 정확히 아는 사람만 찾게 하지 않는다. */
function matches(type: ObjectType, needle: string): boolean {
  if (!needle) return true
  const text = `${type.label} ${type.slug} ${type.description}`.toLowerCase()
  return text.includes(needle.toLowerCase())
}

function TypeRow({ type, depth = 0 }: { type: ObjectType; depth?: number }) {
  const Icon = iconOf(type.icon)
  return (
    <Link
      to={`/o/${type.slug}`}
      className="hover:bg-accent/60 flex items-center gap-2 rounded-md px-2 py-1.5 text-sm"
      style={depth > 0 ? { paddingLeft: `${depth * 1.25 + 0.5}rem` } : undefined}
    >
      <Icon className="text-muted-foreground size-4 shrink-0" />
      <span className="truncate font-medium">{type.label}</span>
      <span className="text-muted-foreground/70 truncate font-mono text-xs">{type.slug}</span>
      {!type.is_active && (
        <Badge variant="outline" className="shrink-0 text-[10px]">
          안 씀
        </Badge>
      )}
      {type.kind_class === 'system' && (
        <Badge variant="outline" className="shrink-0 text-[10px]">
          원 표
        </Badge>
      )}
      {/* **건수를 보여 준다.** 「비어 있는 타입」 과 「만 건이 든 타입」 을 같은 줄로 보여 주면
          어디부터 봐야 하는지를 화면이 말해 주지 못한다. */}
      <span className="text-muted-foreground ml-auto shrink-0 text-xs tabular-nums">
        {type.object_count.toLocaleString()}
      </span>
    </Link>
  )
}

function InterfaceRow({ iface, types }: { iface: ObjectInterface; types: ObjectType[] }) {
  const Icon = iconOf(iface.icon)
  const implementers = types.filter((one) => iface.implementers.includes(one.slug))
  return (
    <div>
      <Link
        to={`/o/${iface.slug}`}
        className="hover:bg-accent/60 flex items-center gap-2 rounded-md px-2 py-1.5 text-sm"
      >
        <Icon className="text-muted-foreground size-4 shrink-0" />
        <span className="truncate font-medium">{iface.label}</span>
        <span className="text-muted-foreground/70 truncate font-mono text-xs">{iface.slug}</span>
        <span className="text-muted-foreground ml-auto shrink-0 text-xs tabular-nums">
          {iface.object_count.toLocaleString()}
        </span>
      </Link>
      {implementers.length > 0 ? (
        <div className="ml-3 border-l pl-3">
          {implementers.map((one) => (
            <TypeRow key={one.slug} type={one} />
          ))}
        </div>
      ) : (
        /* 비어 있는 이유를 그 자리에서 — 줄을 눌러 빈 목록을 보고 나서야 알면 늦다. */
        <p className="text-muted-foreground ml-3 border-l py-1 pl-5 text-xs">
          구현한 타입이 없습니다 — 관리 › 온톨로지에서 타입의 「구현 인터페이스」 로 정합니다.
        </p>
      )}
    </div>
  )
}

function Section({
  title,
  hint,
  children,
  depth = 0,
}: {
  title: string
  hint?: string
  children: React.ReactNode
  depth?: number
}) {
  return (
    <section className={cn(depth > 0 && 'ml-3 border-l pl-3')}>
      {/* **글자 크기는 한 벌이다**(`text-sm`) — 사이드바와 같은 규칙이다. 단계는 들여쓰기 ·
          굵기 · 색으로 가른다: 크기를 섞으면 한 화면에 세 크기가 번갈아 나온다. */}
      <h3
        className={cn(
          'flex items-baseline gap-2 pb-1 text-sm',
          depth === 0 ? 'font-semibold' : 'text-muted-foreground font-medium',
        )}
      >
        <span>{title}</span>
        {hint && <span className="text-muted-foreground text-xs font-normal">{hint}</span>}
      </h3>
      {children}
    </section>
  )
}

export default function ObjectTypesPage() {
  const types = useResource(() => ontologyApi.types(), [])
  const groups = useResource(() => ontologyApi.groups(), [])
  const interfaces = useResource(() => ontologyApi.interfaces(), [])
  const [needle, setNeedle] = useState('')

  const rows = useMemo(() => types.data ?? [], [types.data])
  const shown = useMemo(() => rows.filter((one) => matches(one, needle)), [rows, needle])
  const total = rows.reduce((sum, one) => sum + one.object_count, 0)

  /** 묶음별 — 상위 묶음 › 묶음 › 타입. 묶음에 안 걸린 타입은 맨 끝에 따로. */
  const byGroup = useMemo(() => {
    const all = groups.data ?? []
    const ofGroup = (slug: string) => shown.filter((one) => one.nav_group_slug === slug)
    const tops = all.filter((one) => !one.parent_slug)
    const childrenOf = (slug: string) => all.filter((one) => one.parent_slug === slug)
    const loose = shown.filter(
      (one) => !one.nav_group_slug || !all.some((g) => g.slug === one.nav_group_slug),
    )
    return { tops, ofGroup, childrenOf, loose }
  }, [groups.data, shown])

  /** 인터페이스 — 제 이름이 맞거나, 구현 타입 중 하나가 찾기에 걸리면 선다. */
  const shownInterfaces = useMemo(() => {
    const needleLower = needle.toLowerCase()
    return (interfaces.data ?? []).filter(
      (one) =>
        !needle ||
        `${one.label} ${one.slug} ${one.description}`.toLowerCase().includes(needleLower) ||
        shown.some((type) => one.implementers.includes(type.slug)),
    )
  }, [interfaces.data, needle, shown])

  return (
    <div className="space-y-5">
      <PageHeader
        title="객체 타입 전부"
        description={
          rows.length > 0
            ? `타입 ${rows.length}개 · 객체 ${total.toLocaleString()}건. 줄을 누르면 그 타입의 목록으로 갑니다.`
            : '이 설치에 정의된 객체 타입과 그 건수입니다.'
        }
      />

      <ErrorNotice error={types.error ?? groups.error ?? interfaces.error} />

      <div className="relative max-w-sm">
        <Search className="text-muted-foreground absolute top-2.5 left-2 size-4" />
        <Input
          value={needle}
          onChange={(event) => setNeedle(event.target.value)}
          placeholder="타입 이름 · 식별자로 찾기"
          className="pl-8"
          aria-label="타입 찾기"
        />
      </div>

      {types.data && rows.length === 0 && (
        <EmptyState
          title="정의된 타입이 없습니다"
          hint="관리 › 온톨로지에서 타입을 만들면 여기와 사이드바에 함께 생깁니다."
        />
      )}

      {rows.length > 0 && (
        <Tabs defaultValue="groups">
          <TabsList>
            <TabsTrigger value="groups">묶음별</TabsTrigger>
            {(interfaces.data ?? []).length > 0 && (
              <TabsTrigger value="interfaces">인터페이스</TabsTrigger>
            )}
          </TabsList>

          <TabsContent value="groups" className="space-y-5 pt-3">
            {byGroup.tops.map((top) => {
              const own = byGroup.ofGroup(top.slug)
              const kids = byGroup.childrenOf(top.slug)
              const under = kids.map((kid) => ({ group: kid, types: byGroup.ofGroup(kid.slug) }))
              const any = own.length > 0 || under.some((one) => one.types.length > 0)
              if (!any) return null
              return (
                <Section
                  key={top.slug}
                  title={top.label}
                  hint={!top.is_active ? '안 씀' : undefined}
                >
                  {own.map((one) => (
                    <TypeRow key={one.slug} type={one} />
                  ))}
                  <div className="space-y-2 pt-1">
                    {under.map(({ group, types: inside }) =>
                      inside.length === 0 ? null : (
                        <Section key={group.slug} title={group.label} depth={1}>
                          {inside.map((one) => (
                            <TypeRow key={one.slug} type={one} />
                          ))}
                        </Section>
                      ),
                    )}
                  </div>
                </Section>
              )
            })}

            {byGroup.loose.length > 0 && (
              <Section
                title="묶음에 없는 타입"
                hint="사이드바에는 안 나옵니다 — 관리 › 온톨로지에서 묶음을 정하세요"
              >
                {byGroup.loose.map((one) => (
                  <TypeRow key={one.slug} type={one} />
                ))}
              </Section>
            )}

            {shown.length === 0 && (
              <p className="text-muted-foreground text-sm">「{needle}」 에 맞는 타입이 없습니다.</p>
            )}
          </TabsContent>

          <TabsContent value="interfaces" className="space-y-3 pt-3">
            <p className="text-muted-foreground text-sm">
              여러 타입이 같은 모양으로 따르는 공통 속성의 묶음입니다. 인터페이스를 누르면 구현 타입
              전부의 객체를 한 목록으로 봅니다.
            </p>
            {shownInterfaces.map((one) => (
              <InterfaceRow key={one.slug} iface={one} types={rows} />
            ))}
            {shownInterfaces.length === 0 && (
              <p className="text-muted-foreground text-sm">
                「{needle}」 에 맞는 인터페이스가 없습니다.
              </p>
            )}
          </TabsContent>
        </Tabs>
      )}
    </div>
  )
}
