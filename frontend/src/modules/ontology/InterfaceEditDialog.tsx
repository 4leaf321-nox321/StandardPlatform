/**
 * 인터페이스 하나 — 생성과 수정을 **같은 창이 한다**(ADR 0006).
 *
 * 인터페이스는 여러 타입이 따르는 공통 모양이다. 여기서 정하는 것은 이름 · 설명 · **상위
 * 인터페이스**(그 공통 속성을 이어받는다)이고, 공통 속성은 목록 화면 아래의 편집기가 정한다 —
 * 타입과 같은 길이다.
 *
 * **지우기 전에 무엇이 가리키는지 먼저 본다**(`/usage`). 구현 타입이 남은 채로 지우면 그
 * 타입은 없는 인터페이스를 구현하게 되고, 그 사실은 그 타입을 고치는 날에야 드러난다.
 */

import { useState } from 'react'

import { ontologyApi } from '@/modules/ontology/api'
import type { InterfaceUsage, ObjectInterface } from '@/modules/ontology/api'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { IconPickerButton } from '@/shared/components/IconPicker'
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
import { Textarea } from '@/shared/components/ui/textarea'

interface Props {
  /** 고칠 인터페이스. 없으면 생성이다. */
  iface?: ObjectInterface | null
  /** 상위로 고를 수 있는 것 — 자기 자신은 화면이 뺀다(고리는 서버가 막는다). */
  interfaces: ObjectInterface[]
  onClose: () => void
  onChanged: () => void
}

export function InterfaceEditDialog({ iface, interfaces, onClose, onChanged }: Props) {
  const editing = Boolean(iface)
  const [slug, setSlug] = useState(iface?.slug ?? '')
  const [label, setLabel] = useState(iface?.label ?? '')
  const [icon, setIcon] = useState(iface?.icon || 'Shapes')
  const [description, setDescription] = useState(iface?.description ?? '')
  const [sortOrder, setSortOrder] = useState(String(iface?.sort_order ?? 0))
  const [extendsSlugs, setExtendsSlugs] = useState<string[]>(iface?.extends_slugs ?? [])

  const [error, setError] = useState<Error | null>(null)
  const [saving, setSaving] = useState(false)
  const [usage, setUsage] = useState<InterfaceUsage | null>(null)

  const others = interfaces.filter((one) => one.slug !== iface?.slug)
  const blocking = usage
    ? [
        ...usage.implementers.map((one) => `구현 타입 ${one}`),
        ...usage.sub_interfaces.map((one) => `이어받는 인터페이스 ${one}`),
        ...usage.referenced_by.map((one) => `참조 대상으로 적은 속성 ${one}`),
        ...usage.relation_types.map((one) => `관계 끝에 적은 관계 종류 ${one}`),
      ]
    : []

  async function save() {
    setError(null)
    setSaving(true)
    try {
      const body = {
        label,
        icon,
        description,
        sort_order: Number(sortOrder) || 0,
        extends_slugs: extendsSlugs,
      }
      if (iface) await ontologyApi.updateInterface(iface.slug, body)
      else await ontologyApi.createInterface({ slug, ...body })
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
        {/* 타입 · 속성 · 관계 종류 수정과 **같은 크기**. 스크롤은 `DialogContent` 가 쥔다. */}
        <DialogContent className="h-[80vh] w-[80vw] sm:max-w-[80vw]">
          <DialogHeader>
            <DialogTitle>{editing ? `${iface?.label} 수정` : '인터페이스 생성'}</DialogTitle>
          </DialogHeader>

          <div className="space-y-4">
            {error && <ErrorNotice error={error} />}

            {/* 왼쪽은 **무엇이라 부르나**(slug · 이름 · 설명), 오른쪽은 **무엇을 이어받나**
                (상위 인터페이스). 공통 속성은 목록 아래 편집기에서 정한다. */}
            <div className="grid items-start gap-x-10 gap-y-6 xl:grid-cols-2">
              <section className="space-y-4">
                <h3 className="text-muted-foreground border-b pb-1 text-xs font-semibold">
                  무엇이라 부르나
                </h3>
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <Label htmlFor="iface-slug">slug</Label>
                    <Input
                      id="iface-slug"
                      value={slug}
                      readOnly={editing}
                      disabled={editing}
                      placeholder="equipment"
                      className="font-mono"
                      onChange={(event) => setSlug(event.target.value)}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="iface-label">이름</Label>
                    <Input
                      id="iface-label"
                      value={label}
                      placeholder="설비"
                      onChange={(event) => setLabel(event.target.value)}
                    />
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <Label htmlFor="iface-icon">아이콘</Label>
                    <IconPickerButton id="iface-icon" value={icon} onChange={setIcon} />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="iface-sort">순서</Label>
                    <Input
                      id="iface-sort"
                      type="number"
                      value={sortOrder}
                      onChange={(event) => setSortOrder(event.target.value)}
                    />
                  </div>
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="iface-description">설명</Label>
                  <Textarea
                    id="iface-description"
                    value={description}
                    placeholder="이 인터페이스를 구현하는 타입이 무엇인지 — 「설비라면 제조사 · 모델명 · 교정 만료일이 있다」"
                    onChange={(event) => setDescription(event.target.value)}
                  />
                </div>
              </section>

              <section className="space-y-4">
                <h3 className="text-muted-foreground border-b pb-1 text-xs font-semibold">
                  무엇을 이어받나
                </h3>
                <div className="space-y-1.5">
                  <Label>상위 인터페이스</Label>
                  {others.length === 0 ? (
                    <p className="text-muted-foreground text-sm">다른 인터페이스가 없습니다.</p>
                  ) : (
                    <div className="flex flex-wrap gap-3">
                      {others.map((one) => (
                        <label key={one.slug} className="flex items-center gap-1.5 text-sm">
                          <input
                            type="checkbox"
                            className="size-4"
                            checked={extendsSlugs.includes(one.slug)}
                            onChange={() =>
                              setExtendsSlugs((current) =>
                                current.includes(one.slug)
                                  ? current.filter((s) => s !== one.slug)
                                  : [...current, one.slug],
                              )
                            }
                          />
                          {one.label}
                        </label>
                      ))}
                    </div>
                  )}
                  <p className="text-muted-foreground text-xs">
                    상위 인터페이스의 공통 속성을 <b>이어받습니다</b> — 이 인터페이스를 구현한
                    타입은 그 공통 속성도 갖습니다. 바꾸면 구현 타입 전부가 걸리므로, 모양이 안 맞는
                    타입이 하나라도 있으면 저장이 거절되고 무엇이 다른지 적힙니다.
                  </p>
                </div>
              </section>

              {/* 구현 타입은 설명이 길다 — 단 안에 두면 오른쪽만 길어진다. 두 단 아래 전폭으로. */}
              {iface && (
                <div className="space-y-1 border-t pt-3 xl:col-span-2">
                  <p className="text-sm font-medium">구현 타입 {iface.implementers.length}개</p>
                  <p className="text-muted-foreground text-sm">
                    {iface.implementers.length > 0
                      ? iface.implementers.join(' · ')
                      : '아직 없습니다 — 타입 창의 「구현 인터페이스」 에서 고릅니다.'}
                  </p>
                </div>
              )}
            </div>
          </div>

          <DialogFooter className="justify-between sm:justify-between">
            {editing ? (
              <Button
                variant="ghost"
                disabled={saving}
                onClick={async () => {
                  // **삭제 전에 무엇이 가리키는지 먼저 읽는다.**
                  setUsage(await ontologyApi.interfaceUsage(iface!.slug))
                }}
              >
                삭제
              </Button>
            ) : (
              <span />
            )}
            <div className="flex gap-2">
              <Button variant="outline" onClick={onClose} disabled={saving}>
                취소
              </Button>
              <Button onClick={save} disabled={saving || !slug.trim() || !label.trim()}>
                {editing ? '저장' : '생성'}
              </Button>
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {usage && iface && (
        <ConfirmDialog
          open
          destructive={blocking.length === 0}
          title={
            blocking.length > 0
              ? `${iface.label}을(를) 지금은 삭제할 수 없습니다`
              : `${iface.label} 인터페이스를 삭제합니다`
          }
          description={
            blocking.length > 0 ? (
              <div className="space-y-2">
                <p>
                  가리키는 것이 <b>{blocking.length}개</b> 있습니다. 먼저 해제하세요 — 남긴 채
                  삭제하면 그쪽이 없는 인터페이스를 가리키게 됩니다.
                </p>
                <ul className="text-muted-foreground list-inside list-disc">
                  {blocking.map((one) => (
                    <li key={one}>{one}</li>
                  ))}
                </ul>
              </div>
            ) : (
              <p>
                공통 속성 정의도 함께 사라집니다. <b>구현했던 타입의 속성은 남습니다</b>(지금은 구현
                타입이 없습니다).
              </p>
            )
          }
          confirmLabel={blocking.length > 0 ? '닫기' : '삭제'}
          onConfirm={async () => {
            if (blocking.length > 0) return
            await ontologyApi.removeInterface(iface.slug)
            onChanged()
            onClose()
          }}
          onClose={() => setUsage(null)}
        />
      )}
    </>
  )
}
