/**
 * 타입 하나를 고친다 — **행을 누르면 여기가 뜬다.**
 *
 * 고칠 자리가 없으면 잘못 넣은 타입을 옮길 방법이 없고, 그러면 사람은 새로
 * 만들고 옛것을 버려 둔다. 버려진 것은 목록에 남아 다음 사람을 헷갈리게 한다.
 *
 * **속성 정의는 여기 없다.** 그것은 그것대로 자기 자리가 있다 — 한 창에 섞으면
 * 「타입의 설정」 과 「그 타입이 담는 값의 모양」 이 같은 것처럼 읽힌다.
 */

import { useState } from 'react'

import { ListViewEditor } from '@/modules/ontology/ListViewEditor'
import { SectionViewEditor } from '@/modules/ontology/SectionViewEditor'
import { ontologyApi } from '@/modules/ontology/api'
import type {
  ImplementPlan,
  ListView,
  NavGroupRow,
  ObjectInterface,
  ObjectType,
  PropertyDef,
  RelationType,
  SectionView,
  SystemSource,
} from '@/modules/ontology/api'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { IconPicker } from '@/shared/components/IconPicker'
import { Button } from '@/shared/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/components/ui/dialog'
import { Input } from '@/shared/components/ui/input'
import { Label } from '@/shared/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/shared/components/ui/tabs'
import { Textarea } from '@/shared/components/ui/textarea'

/** 「묶음 없음」. **빈 문자열을 쓸 수 없다** — Select 가 그것을 「고른 것 없음」 으로 본다. */
const NONE = '__none__'

interface Props {
  /** 속성 정의까지 들고 온다 — **열로 고를 것이 그 목록에서 나온다.** */
  type: ObjectType & { properties: PropertyDef[] }
  groups: NavGroupRow[]
  /** 구현할 수 있는 인터페이스(ADR 0006). 없으면 「구현 인터페이스」 칸이 안 선다. */
  interfaces?: ObjectInterface[]
  relationTypes: RelationType[]
  /** 투영이 비출 수 있는 원 표. 스키마가 준다 — 등록 안 된 표는 고를 수 없다. */
  systemSources?: SystemSource[]
  onClose: () => void
  onChanged: () => void
}

export function TypeEditDialog({
  type,
  groups,
  interfaces = [],
  relationTypes,
  systemSources = [],
  onClose,
  onChanged,
}: Props) {
  const [label, setLabel] = useState(type.label)
  const [icon, setIcon] = useState(type.icon || 'LayoutGrid')
  const [description, setDescription] = useState(type.description)
  const [group, setGroup] = useState(type.nav_group_slug ?? NONE)
  const [kindClass, setKindClass] = useState<string>(type.kind_class)
  const [implemented, setImplemented] = useState<string[]>(type.interface_slugs ?? [])
  /** 구현을 바꾸면 **저장 전에** 무엇이 되는지 — 만들 속성 · 채택할 속성 · 충돌. */
  const [implementPlan, setImplementPlan] = useState<ImplementPlan | null>(null)
  const implementChanged = !sameSet(implemented, type.interface_slugs ?? [])
  const [systemSource, setSystemSource] = useState<string>(
    type.system_source || systemSources[0]?.key || '',
  )
  const [entryPolicy, setEntryPolicy] = useState<string>(type.entry_policy)
  const [keyPolicy, setKeyPolicy] = useState<string>(type.key_policy)
  const [keyScope, setKeyScope] = useState<string>(type.key_scope)
  const [temporalKind, setTemporalKind] = useState<string>(type.temporal_kind)
  const [sortOrder, setSortOrder] = useState(String(type.sort_order))
  const [isActive, setIsActive] = useState(type.is_active)
  const [core, setCore] = useState(type.core ?? false)
  const [listView, setListView] = useState<ListView>(type.list_view ?? {})
  const [formView, setFormView] = useState<SectionView>(type.form_view ?? {})
  const [detailView, setDetailView] = useState<SectionView>(type.detail_view ?? {})

  const [error, setError] = useState<Error | null>(null)
  const [saving, setSaving] = useState(false)
  const [removing, setRemoving] = useState(false)

  async function save() {
    setError(null)
    setSaving(true)
    try {
      // **보낸 것만 바뀐다.** 이 창이 아는 칸만 실어 보낸다.
      await ontologyApi.updateType(type.slug, {
        label,
        description,
        icon,
        nav_group_slug: group === NONE ? null : group,
        // **바꾼 때만 보낸다** — 보내면 구현을 다시 맞추고 기록을 남긴다.
        ...(implementChanged ? { interface_slugs: implemented } : {}),
        kind_class: kindClass,
        system_source: kindClass === 'system' ? systemSource : '',
        entry_policy: entryPolicy,
        key_policy: keyPolicy,
        key_scope: keyScope,
        temporal_kind: temporalKind,
        sort_order: Number(sortOrder) || 0,
        is_active: isActive,
        core,
        list_view: listView,
        form_view: formView,
        detail_view: detailView,
      })
      onChanged()
      onClose()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      <Dialog open onOpenChange={(open) => !open && onClose()}>
        {/* **크게 연다.** 정의를 고치는 창은 셋 다 같은 크기다 — 속성 하나를 고치러
            들어갔는데 창이 달라지면 어디를 보고 있었는지 잃는다.

            **스크롤은 하나뿐이다.** `DialogContent` 가 이미 머리·바닥을 붙박이로 두고 가운데를
            굴린다(`ui/dialog.tsx`). 여기서 `overflow-y-auto` 를 또 두면 스크롤바가 둘이 되고,
            그때 어느 것을 굴려야 하는지는 해 보기 전에는 모른다. */}
        <DialogContent className="h-[80vh] w-[80vw] sm:max-w-[80vw]">
          <DialogHeader>
            <DialogTitle>{type.label} 수정</DialogTitle>
          </DialogHeader>

          {error && <ErrorNotice error={error} />}

          {/* 탭 바는 붙박이고 **탭 내용만** 굴린다 — 그래야 바깥(모달 가운데)이 안 넘치고
              스크롤바가 하나로 남는다. */}
          <Tabs defaultValue="basic" className="flex min-h-0 flex-1 flex-col">
            <TabsList>
              <TabsTrigger value="basic">설정</TabsTrigger>
              {/* **목록 화면은 타입의 설정이지만 성격이 다르다.** 한 폼에 이어
                  붙이면 스크롤이 길어져 아래 절반을 아무도 안 본다. */}
              <TabsTrigger value="list">목록 화면</TabsTrigger>
              <TabsTrigger value="form">폼·상세</TabsTrigger>
            </TabsList>

            <TabsContent value="basic" className="min-h-0 flex-1 overflow-y-auto pr-1 pb-1">
              {/* 왼쪽은 **이 타입이 무엇인가**(이름 · 설명 · 그림 · 사이드바 자리), 오른쪽은
                  **어떻게 다루나**(분류 · 정책). 한 단으로 두면 오른쪽 절반이 통째로 비고
                  스크롤만 길어진다. */}
              <div className="grid items-start gap-x-10 gap-y-6 xl:grid-cols-2">
                <section className="space-y-4">
                  <h3 className="text-muted-foreground border-b pb-1 text-xs font-semibold">
                    무엇인가
                  </h3>
                  <div className="space-y-1.5">
                    <Label>slug</Label>
                    <Input value={type.slug} readOnly disabled className="font-mono" />
                    <p className="text-muted-foreground text-xs">
                      <b>바꿀 수 없습니다.</b> 주소(<code>/o/{type.slug}</code>
                      )와 관계·MCP 도구 이름이 여기 물려 있어, 바꾸면 그 셋이 조용히 어긋납니다.
                    </p>
                  </div>

                  <div className="space-y-1.5">
                    <Label htmlFor="type-edit-label">이름</Label>
                    <Input
                      id="type-edit-label"
                      value={label}
                      onChange={(event) => setLabel(event.target.value)}
                    />
                  </div>

                  <div className="space-y-1.5">
                    <Label htmlFor="type-edit-description">설명</Label>
                    <Textarea
                      id="type-edit-description"
                      rows={2}
                      value={description}
                      onChange={(event) => setDescription(event.target.value)}
                    />
                  </div>

                  <div className="space-y-1.5">
                    <Label htmlFor="type-edit-icon">아이콘</Label>
                    {/* 사이드바와 목록에 서는 그림. 전부 같은 네모면 타입이 열둘쯤 될 때
                    이름을 한 자씩 읽어야 한다 — 눈은 모양을 먼저 잡는다. */}
                    <IconPicker id="type-edit-icon" value={icon} onChange={setIcon} />
                  </div>

                  {interfaces.length > 0 && (
                    <div className="space-y-1.5">
                      <Label>구현 인터페이스</Label>
                      <div className="flex flex-wrap gap-3">
                        {interfaces.map((one) => (
                          <label key={one.slug} className="flex items-center gap-1.5 text-sm">
                            <input
                              type="checkbox"
                              className="size-4"
                              disabled={kindClass === 'system'}
                              checked={implemented.includes(one.slug)}
                              onChange={() => {
                                const next = implemented.includes(one.slug)
                                  ? implemented.filter((s) => s !== one.slug)
                                  : [...implemented, one.slug]
                                setImplemented(next)
                                setImplementPlan(null)
                                if (sameSet(next, type.interface_slugs ?? [])) return
                                ontologyApi
                                  .implementPlan(type.slug, next)
                                  .then(setImplementPlan)
                                  .catch((caught) =>
                                    setError(
                                      caught instanceof Error
                                        ? caught
                                        : new Error('알 수 없는 오류'),
                                    ),
                                  )
                              }}
                            />
                            {one.label}
                          </label>
                        ))}
                      </div>
                      <p className="text-muted-foreground text-xs">
                        {kindClass === 'system'
                          ? '다른 표를 비추는 타입(투영)은 인터페이스를 구현하지 않습니다 — 속성이 없습니다.'
                          : '인터페이스의 공통 속성을 같은 키 · 같은 모양으로 갖습니다. 없는 속성은 만들고, 같은 모양이면 그대로 채택하고, 다르면 저장되지 않습니다. 해제해도 속성은 남습니다.'}
                      </p>
                      {implementPlan && <ImplementPreview plan={implementPlan} />}
                    </div>
                  )}
                </section>

                <section className="space-y-4">
                  <h3 className="text-muted-foreground border-b pb-1 text-xs font-semibold">
                    어디에 · 어떻게
                  </h3>
                  <div className="space-y-1.5">
                    <Label htmlFor="type-edit-group">사이드바 묶음</Label>
                    <Select value={group} onValueChange={setGroup}>
                      <SelectTrigger id="type-edit-group">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value={NONE}>사이드바에 안 세움</SelectItem>
                        {groups.map((one) => (
                          <SelectItem key={one.slug} value={one.slug}>
                            {one.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <p className="text-muted-foreground text-xs">
                      표시하지 않으면 화면은 있지만 메뉴에 표시되지 않습니다 — 어휘 축은 대개
                      그렇습니다.
                    </p>
                  </div>

                  <div className="grid gap-x-4 gap-y-4 sm:grid-cols-2">
                    <Field
                      label="객체 분류"
                      value={kindClass}
                      onChange={setKindClass}
                      options={[
                        ['record', '객체'],
                        ['reference', '어휘'],
                        ['system', '투영'],
                      ]}
                    />
                    {/* 투영은 **어느 표를 비추는지**가 전부다 — 행이 없고, 목록·상세·참조가
                  그 표에서 나온다. 등록된 표가 없으면 고를 것도 없다. */}
                    {kindClass === 'system' && (
                      <Field
                        label="비추는 원 표"
                        value={systemSource}
                        onChange={setSystemSource}
                        options={systemSources.map((one) => [one.key, `${one.label} (${one.key})`])}
                      />
                    )}
                    <Field
                      label="입력 정책"
                      value={entryPolicy}
                      onChange={setEntryPolicy}
                      options={[
                        ['open', '누구나 추가'],
                        ['closed', '관리자만'],
                      ]}
                    />
                    <Field
                      label="식별자"
                      value={keyPolicy}
                      onChange={setKeyPolicy}
                      options={[
                        ['none', '안 씀'],
                        ['optional', '선택'],
                        ['required', '필수'],
                      ]}
                    />
                    <Field
                      label="식별자 범위"
                      value={keyScope}
                      onChange={setKeyScope}
                      options={[
                        ['global', '전사에서 하나'],
                        ['workspace', '부서에서 하나'],
                      ]}
                    />
                    <Field
                      label="시간 정책"
                      value={temporalKind}
                      onChange={setTemporalKind}
                      options={[
                        ['evergreen', '연도 무관'],
                        ['lifecycle', '유효 구간'],
                        ['yearly', '연도 배정'],
                        ['derived', '쓰인 데서 추론'],
                      ]}
                    />
                    <div className="space-y-1.5">
                      <Label htmlFor="type-edit-sort">순서</Label>
                      <Input
                        id="type-edit-sort"
                        type="number"
                        value={sortOrder}
                        onChange={(event) => setSortOrder(event.target.value)}
                      />
                    </div>
                  </div>
                </section>

                {/* 설명이 길어 단 안에 두면 한쪽만 늘어난다 — 두 단 아래 전폭으로. */}
                <label className="flex items-center gap-2 border-t pt-3 text-sm xl:col-span-2">
                  <input
                    type="checkbox"
                    className="size-4"
                    checked={isActive}
                    onChange={(event) => setIsActive(event.target.checked)}
                  />
                  사용함
                  <span className="text-muted-foreground text-xs">
                    끄면 메뉴와 생성에서 빠집니다. <b>자료는 그대로 남습니다.</b>
                  </span>
                </label>

                {/* **공개는 화면 배치와 따로 정한다.** 사이드바 묶음을 공유 경계로 쓰면
                    누가 메뉴를 옮기는 순간 바깥에 열린 범위가 조용히 바뀐다. */}
                {kindClass !== 'system' && (
                  <label className="flex items-start gap-2 border-t pt-3 text-sm xl:col-span-2">
                    <input
                      type="checkbox"
                      className="mt-0.5 size-4"
                      checked={core}
                      onChange={(event) => setCore(event.target.checked)}
                    />
                    <span>
                      코어 — 외부 시스템에 공개
                      <span className="text-muted-foreground ml-1 text-xs">
                        지정하면 이 타입이 <code>/api/core</code> 로 공개되어 외부 시스템이
                        주기적으로 조회합니다(조회 권한 범위 내). <b>지정하는 순간 약속이 됩니다</b>{' '}
                        — 타입 slug 와 속성 key 가 외부 시스템 코드에 사용되므로, 이후 이름을
                        변경하면 해당 시스템이 동작하지 않습니다.
                      </span>
                    </span>
                  </label>
                )}
              </div>
            </TabsContent>

            <TabsContent value="list" className="min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">
              <ListViewEditor
                defs={type.properties}
                relationTypes={relationTypes}
                typeSlug={type.slug}
                value={listView}
                onChange={setListView}
              />
            </TabsContent>

            <TabsContent value="form" className="min-h-0 flex-1 space-y-6 overflow-y-auto pr-1">
              <div className="space-y-2">
                <Label>생성·수정 폼</Label>
                <SectionViewEditor
                  defs={type.properties}
                  value={formView}
                  onChange={setFormView}
                  what="폼"
                />
              </div>
              <div className="space-y-2 border-t pt-4">
                <Label>객체 상세</Label>
                <SectionViewEditor
                  defs={type.properties}
                  value={detailView}
                  onChange={setDetailView}
                  what="상세"
                />
              </div>
            </TabsContent>
          </Tabs>

          <DialogFooter className="justify-between sm:justify-between">
            <Button variant="ghost" onClick={() => setRemoving(true)} disabled={saving}>
              삭제
            </Button>
            <div className="flex gap-2">
              <Button variant="outline" onClick={onClose} disabled={saving}>
                취소
              </Button>
              <Button
                onClick={save}
                disabled={saving || !label.trim() || (implementPlan?.conflicts.length ?? 0) > 0}
              >
                저장
              </Button>
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {removing && (
        <ConfirmDialog
          open
          destructive
          title={`${type.label} 타입을 삭제합니다`}
          description={
            type.object_count > 0 ? (
              <>
                지금 <b>{type.object_count}개</b>가 들어 있어 <b>지울 수 없습니다.</b> 그만 쓰려는
                것이면 「사용함」 을 끄세요 — 자료는 남고 화면에서만 빠집니다.
              </>
            ) : (
              <>
                들어 있는 것이 없어 삭제할 수 있습니다. <b>속성 정의도 함께 사라집니다</b> — 안
                삭제하면 같은 slug 로 다시 만들 때 옛 속성이 되살아납니다.
              </>
            )
          }
          confirmLabel="삭제"
          onConfirm={async () => {
            await ontologyApi.removeType(type.slug)
            onChanged()
            onClose()
          }}
          onClose={() => setRemoving(false)}
        />
      )}
    </>
  )
}

function Field({
  label,
  value,
  onChange,
  options,
}: {
  label: string
  value: string
  onChange: (next: string) => void
  options: [string, string][]
}) {
  return (
    <div className="space-y-1.5">
      <Label>{label}</Label>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map(([key, text]) => (
            <SelectItem key={key} value={key}>
              {text}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}

function sameSet(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((one) => b.includes(one))
}

/** 구현하면 무엇이 되는지 — **충돌은 서버가 적은 그대로** 보인다(무엇이 다른지가 거기 있다). */
function ImplementPreview({ plan }: { plan: ImplementPlan }) {
  const keys = (rows: { key: string }[]) => rows.map((one) => one.key).join(', ')
  return (
    <div className="space-y-1 rounded-md border p-2 text-xs" role="status">
      {plan.creates.length > 0 && (
        <p>
          <b>새로 만들 속성</b> {keys(plan.creates)}
        </p>
      )}
      {plan.adopts.length > 0 && (
        <p>
          <b>그대로 채택할 속성</b> {keys(plan.adopts)}
        </p>
      )}
      {plan.creates.length === 0 && plan.adopts.length === 0 && plan.conflicts.length === 0 && (
        <p className="text-muted-foreground">바뀌는 속성이 없습니다.</p>
      )}
      {plan.warnings.map((one) => (
        <p key={one} className="text-amber-700 dark:text-amber-400">
          {one}
        </p>
      ))}
      {plan.conflicts.length > 0 && (
        <div className="text-destructive space-y-0.5">
          <p className="font-medium">구현할 수 없습니다 — 모양이 다릅니다</p>
          {plan.conflicts.map((one) => (
            <p key={one}>{one}</p>
          ))}
        </div>
      )}
    </div>
  )
}
