/**
 * 속성 정의 하나 — 생성과 수정을 **같은 창이 한다.**
 *
 * 두 벌로 만들면 칸이 갈리고, 갈린 것은 한쪽만 고쳐진다. 그러면 「만들 때는
 * 정할 수 있는데 고칠 때는 없는 칸」 이 생기고, 그것을 고치려면 지웠다 다시
 * 만들어야 한다 — 그 순간 그 속성의 값이 전부 화면에서 사라진다.
 *
 * **키는 만들 때만 정한다** — 바꾸면 이미 저장된 값이 전부 고아가 된다. **종류는 저장으로
 * 바꾸지 않는다**: 종류만 바뀌면 그 값들이 새 종류에 안 맞는데 화면은 아무 말도 안 한다. 바꾸는
 * 길은 「종류 변경」(ADR 0007) — 저장값을 변환하는 계획을 먼저 본다(`RetypeDialog`).
 *
 * **인터페이스의 공통 속성도 이 창이다**(ADR 0006) — 두 벌이면 위젯이 갈린다. 다른 점은 둘:
 * 공통 속성에는 타입마다 정하는 칸(유일 · 기본값 · 역방향 이름)이 없고, 타입 쪽에서 공통 속성을
 * 열면 **모양 칸이 잠기고 왜 잠겼는지 적힌다**(비활성만 시키면 버그로 읽힌다).
 */

import { useState } from 'react'

import { ontologyApi } from '@/modules/ontology/api'
import { EnumOptionsPanel } from '@/modules/ontology/EnumOptionsPanel'
import { RetypeDialog, retypeChoices } from '@/modules/ontology/RetypeDialog'
import type { DataType, ObjectInterface, ObjectType, PropertyDef } from '@/modules/ontology/api'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
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
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from '@/shared/components/ui/select'

export const DATA_TYPE_LABELS: Record<DataType, string> = {
  text: '글 (한 줄)',
  text_long: '글 (여러 줄)',
  number: '숫자',
  date: '날짜',
  datetime: '날짜와 시각',
  bool: '예/아니오',
  enum: '선택',
  url: '주소',
  object_ref: '객체 참조',
  file: '파일',
}

/** 종류마다 무엇이 되는지 한 줄. **선택 전에 알아야 고를 수 있다.** */
const DATA_TYPE_HINTS: Record<DataType, string> = {
  text: '한 줄. 이름·번호처럼 짧은 값. 모양 규칙을 걸 수 있습니다.',
  text_long: '여러 줄. 설명·메모 — 한 줄 칸에 넣으면 사람은 자기가 쓴 것을 못 봅니다.',
  number: '숫자만. 정렬과 집계가 됩니다 — 글로 넣으면 사전순으로 섞입니다.',
  date: 'YYYY-MM-DD. 날짜 선택기가 표시됩니다.',
  datetime: '날짜와 시각. 측정·기록 시각처럼 날짜만으로 부족한 자리에 씁니다.',
  bool: '예/아니오 하나.',
  enum: '정한 것 중에서 고릅니다. 스물이 넘으면 검색 가능한 picker 로 바뀝니다.',
  url: '링크로 열립니다. http:// 나 https:// 로 시작해야 합니다.',
  object_ref: '다른 객체를 가리킵니다. 가리킬 타입을 정해 두면 그 안에서만 고릅니다.',
  file: '첨부로 올립니다. 값이 아니라 파일이라 저장한 뒤 상세 화면에서 붙입니다.',
}

/** 규칙 칸을 보여 줄 종류. **안 쓰이는 칸을 세우면 그것이 뭔가 하는 줄 안다.** */
const NUMERIC = new Set<DataType>(['number'])
const PATTERNABLE = new Set<DataType>(['text', 'text_long', 'url'])
const UNIQUEABLE = new Set<DataType>(['text', 'number', 'url', 'date', 'datetime'])

/** 속성이 붙는 자리 — 타입이거나 인터페이스(공통 속성). */
export type PropertyOwner =
  { kind: 'type'; row: ObjectType } | { kind: 'interface'; row: ObjectInterface }

interface Props {
  owner: PropertyOwner
  /** 고칠 속성. 없으면 생성이다. */
  property?: PropertyDef | null
  /** 「객체 참조」 가 가리킬 수 있는 타입들. */
  types: ObjectType[]
  /** 「객체 참조」 가 가리킬 수 있는 인터페이스 — 그것을 구현한 타입의 객체를 고른다. */
  interfaces?: ObjectInterface[]
  onClose: () => void
  onChanged: () => void
}

export function PropertyEditDialog({
  owner,
  property,
  types,
  interfaces = [],
  onClose,
  onChanged,
}: Props) {
  const editing = Boolean(property)
  const isInterface = owner.kind === 'interface'
  /** 타입 쪽에서 연 공통 속성 — 모양은 그 인터페이스에서 고친다. */
  const boundTo = owner.kind === 'type' ? (property?.interface_slug ?? null) : null
  const locked = boundTo !== null
  /** 종류 변경을 못 하는 까닭 — 단추를 잠그고 그 자리에 적는다(잠그기만 하면 버그로 읽힌다). */
  const retypeBlocked: string | null = !property
    ? null
    : locked
      ? `인터페이스 ${boundTo} 의 공통 속성이라 종류는 인터페이스에서 변경합니다.`
      : owner.row.managed_by
        ? `${owner.row.managed_by} 가 관리하는 정의라 여기서 변경하지 않습니다.`
        : retypeChoices(property.data_type).length === 0
          ? '파일 속성은 값이 첨부라 종류를 변경하지 않습니다.'
          : null

  const [key, setKey] = useState(property?.key ?? '')
  const [label, setLabel] = useState(property?.label ?? '')
  const [dataType, setDataType] = useState<DataType>(property?.data_type ?? 'text')
  const [unit, setUnit] = useState(property?.unit ?? '')
  const [help, setHelp] = useState(property?.help ?? '')
  const [required, setRequired] = useState(property?.required ?? false)
  const [multi, setMulti] = useState(property?.multi ?? false)
  const [sortOrder, setSortOrder] = useState(String(property?.sort_order ?? 0))
  const [options, setOptions] = useState((property?.enum_options ?? []).join(', '))
  const [minValue, setMinValue] = useState(property?.min_value?.toString() ?? '')
  const [maxValue, setMaxValue] = useState(property?.max_value?.toString() ?? '')
  const [decimals, setDecimals] = useState(property?.decimals?.toString() ?? '')
  const [pattern, setPattern] = useState(property?.pattern ?? '')
  const [imageOnly, setImageOnly] = useState(property?.accept === 'image')
  const [defaultValue, setDefaultValue] = useState(
    property?.default_value == null
      ? ''
      : Array.isArray(property.default_value)
        ? property.default_value.map(String).join(', ')
        : String(property.default_value),
  )
  const [unique, setUnique] = useState(property?.unique ?? false)
  const [refType, setRefType] = useState(property?.ref_type_slug ?? '')
  const [inverseLabel, setInverseLabel] = useState(property?.inverse_label ?? '')

  const [error, setError] = useState<Error | null>(null)
  const [saving, setSaving] = useState(false)
  const [removing, setRemoving] = useState(false)
  const [retyping, setRetyping] = useState(false)
  /** 바깥에 열린 타입이면 **누가 읽을 수 있는지** — 아무도 안 쓴다고 여기고 누르지 않게. */
  const [opened, setOpened] = useState<string[] | null>(null)
  const [usage, setUsage] = useState<number | null>(null)

  function body(): Record<string, unknown> {
    return {
      key,
      label,
      data_type: dataType,
      unit,
      help,
      required,
      multi,
      sort_order: Number(sortOrder) || 0,
      // **묶음(폼 · 상세의 섹션)은 이 창에서 안 고치지만 그대로 실어 보낸다** — 서버의 PATCH 는
      // 통째 교체라, 빼면 이름 한 글자만 고쳐도 그 속성이 묶음에서 빠졌다(2026-10-08).
      section: property?.section ?? '',
      min_value: NUMERIC.has(dataType) && minValue !== '' ? Number(minValue) : null,
      max_value: NUMERIC.has(dataType) && maxValue !== '' ? Number(maxValue) : null,
      decimals: NUMERIC.has(dataType) && decimals !== '' ? Number(decimals) : null,
      pattern: PATTERNABLE.has(dataType) && pattern ? pattern : null,
      accept: dataType === 'file' && imageOnly ? 'image' : null,
      // **빈 칸은 「기본값 없음」 이다.** 빈 문자열을 넣으면 그것이 기본값이 되고,
      // 그러면 필수 검사가 통과해 버린다. 공통 속성에는 기본값 · 유일이 없다(타입마다다).
      default_value:
        isInterface || defaultValue === ''
          ? null
          : coerceDefault(dataType, defaultValue, multi),
      unique: !isInterface && UNIQUEABLE.has(dataType) ? unique : false,
      enum_options:
        dataType === 'enum'
          ? options
              .split(',')
              .map((one) => one.trim())
              .filter(Boolean)
          : null,
      ref_type_slug: dataType === 'object_ref' ? refType || null : null,
      inverse_label: !isInterface && dataType === 'object_ref' ? inverseLabel.trim() : '',
    }
  }

  const calls =
    owner.kind === 'interface'
      ? {
          create: (b: Record<string, unknown>) =>
            ontologyApi.createInterfaceProperty(owner.row.slug, b),
          update: (k: string, b: Record<string, unknown>) =>
            ontologyApi.updateInterfaceProperty(owner.row.slug, k, b),
          usage: (k: string) => ontologyApi.interfacePropertyUsage(owner.row.slug, k),
          remove: (k: string) => ontologyApi.removeInterfaceProperty(owner.row.slug, k),
        }
      : {
          create: (b: Record<string, unknown>) => ontologyApi.createProperty(owner.row.slug, b),
          update: (k: string, b: Record<string, unknown>) =>
            ontologyApi.updateProperty(owner.row.slug, k, b),
          usage: (k: string) => ontologyApi.propertyUsage(owner.row.slug, k),
          remove: (k: string, acceptCore: boolean) =>
            ontologyApi.removeProperty(owner.row.slug, k, acceptCore),
        }

  async function save() {
    setError(null)
    setSaving(true)
    try {
      if (property) await calls.update(property.key, body())
      else await calls.create(body())
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
        {/* 타입 · 관계 종류 수정과 **같은 크기**. 스크롤은 `DialogContent` 가 쥔다 —
            여기서 또 굴리면 스크롤바가 둘이 된다(`ui/dialog.tsx`). */}
        <DialogContent className="h-[80vh] w-[80vw] sm:max-w-[80vw]">
          <DialogHeader>
            <DialogTitle>
              {owner.row.label}
              {isInterface ? ' (인터페이스)' : ''} ·{' '}
              {editing ? `${property?.label} 수정` : isInterface ? '공통 속성 추가' : '속성 추가'}
            </DialogTitle>
          </DialogHeader>

          <div className="space-y-4">
            {error && <ErrorNotice error={error} />}
            {locked && (
              <p className="rounded-md border border-sky-500/40 bg-sky-500/5 p-2 text-sm">
                인터페이스 <b className="font-mono">{boundTo}</b> 의 <b>공통 속성</b>입니다 — 종류 ·
                선택할 값 · 단위 · 범위 · 규칙 · 여러 값은 <b>인터페이스에서 수정</b>합니다(한
                타입만 바뀌면 같은 속성이 타입마다 갈립니다). 이름 · 안내 · 순서 · 기본값 · 유일은
                이 타입에서 정합니다.
              </p>
            )}
            {isInterface && (
              <p className="text-muted-foreground text-xs">
                공통 속성은 <b>구현 타입 전부에 같은 키로</b> 섭니다. 여기서 모양을 고치면 구현
                타입들의 속성도 함께 바뀝니다.
              </p>
            )}

            {/* 왼쪽은 **무엇을 담나**(키 · 이름 · 종류, 그리고 그 종류가 부르는 칸),
                오른쪽은 **어떻게 담나**(단위 · 범위 · 규칙 · 안내). 종류를 바꾸면 왼쪽만
                늘었다 줄었다 하고, 오른쪽은 제자리에 있다. */}
            <div className="grid items-start gap-x-10 gap-y-6 xl:grid-cols-2">
              <section className="space-y-4">
                <h3 className="text-muted-foreground border-b pb-1 text-xs font-semibold">
                  무엇을 담나
                </h3>
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <Label htmlFor="prop-key">키</Label>
                    <Input
                      id="prop-key"
                      value={key}
                      readOnly={editing}
                      disabled={editing}
                      placeholder="vendor"
                      className="font-mono"
                      onChange={(event) => setKey(event.target.value)}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="prop-label">이름</Label>
                    <Input
                      id="prop-label"
                      value={label}
                      placeholder="공급사"
                      onChange={(event) => setLabel(event.target.value)}
                    />
                  </div>
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="prop-type">종류</Label>
                  {editing && property ? (
                    <>
                      <div className="flex gap-2">
                        <Input value={DATA_TYPE_LABELS[dataType]} readOnly disabled />
                        <Button
                          type="button"
                          variant="outline"
                          disabled={Boolean(retypeBlocked)}
                          onClick={() => setRetyping(true)}
                        >
                          종류 변경
                        </Button>
                      </div>
                      <p className="text-muted-foreground text-xs">
                        {retypeBlocked ??
                          '종류는 저장으로 바뀌지 않습니다 — 「종류 변경」 이 저장된 값을 새 종류로 변환하는 계획을 먼저 보여 줍니다.'}
                      </p>
                    </>
                  ) : (
                    <>
                      <Select
                        value={dataType}
                        onValueChange={(next) => setDataType(next as DataType)}
                      >
                        <SelectTrigger id="prop-type">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {(Object.keys(DATA_TYPE_LABELS) as DataType[])
                            // 파일은 값이 속성 칸에 없어 여러 타입을 한 목록으로 묻지 못한다.
                            .filter((one) => !(isInterface && one === 'file'))
                            .map((one) => (
                              <SelectItem key={one} value={one}>
                                {DATA_TYPE_LABELS[one]}
                              </SelectItem>
                            ))}
                        </SelectContent>
                      </Select>
                      <p className="text-muted-foreground text-xs">{DATA_TYPE_HINTS[dataType]}</p>
                    </>
                  )}
                </div>

                {dataType === 'enum' && (
                  <div className="space-y-1.5">
                    <Label htmlFor="prop-options">선택할 값 (쉼표로)</Label>
                    <Input
                      id="prop-options"
                      value={options}
                      disabled={locked}
                      placeholder="A, B, C"
                      onChange={(event) => setOptions(event.target.value)}
                    />
                    <p className="text-muted-foreground text-xs">
                      이미 사용되는 값을 목록에서 제외하면{' '}
                      <b>그 값을 가진 객체는 수정할 때 거절됩니다.</b>
                      제외하기 전에 그 값을 사용하는 것이 있는지 보세요.
                    </p>
                  </div>
                )}

                {/* 저장된 속성의 고를 값 — 이름을 바꾸면 저장값도 함께, 코드표로 승격. */}
                {editing &&
                  !locked &&
                  property &&
                  property.data_type === 'enum' &&
                  dataType === 'enum' && (
                    <EnumOptionsPanel
                      type={owner.kind === 'type' ? owner.row : undefined}
                      interfaceSlug={owner.kind === 'interface' ? owner.row.slug : undefined}
                      property={property}
                      types={types}
                      onChanged={(renamed) => {
                        if (renamed) {
                          // 이 창의 「고를 값」 칸도 같이 — 안 그러면 「저장」 이 옛 목록을 다시 보낸다.
                          setOptions((current) =>
                            current
                              .split(',')
                              .map((one) => one.trim())
                              .filter(Boolean)
                              .map((one) => (one === renamed.from ? renamed.to : one))
                              .join(', '),
                          )
                          setDefaultValue((current) =>
                            current === renamed.from ? renamed.to : current,
                          )
                        }
                        onChanged()
                      }}
                      onPromoted={() => {
                        onChanged()
                        onClose()
                      }}
                    />
                  )}

                {dataType === 'object_ref' && (
                  <div className="space-y-1.5">
                    <Label htmlFor="prop-ref">가리킬 타입</Label>
                    <Select value={refType} onValueChange={setRefType} disabled={locked}>
                      <SelectTrigger id="prop-ref">
                        <SelectValue placeholder={isInterface ? '선택하세요' : '아무 타입이나'} />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectGroup>
                          <SelectLabel>타입</SelectLabel>
                          {types.map((one) => (
                            <SelectItem key={one.slug} value={one.slug}>
                              {one.label}
                            </SelectItem>
                          ))}
                        </SelectGroup>
                        {interfaces.length > 0 && (
                          <SelectGroup>
                            <SelectLabel>인터페이스 — 구현한 타입 중에서</SelectLabel>
                            {interfaces.map((one) => (
                              <SelectItem key={one.slug} value={one.slug}>
                                {one.label} (구현 타입 {one.implementers.length})
                              </SelectItem>
                            ))}
                          </SelectGroup>
                        )}
                      </SelectContent>
                    </Select>
                    <p className="text-muted-foreground text-xs">
                      {isInterface
                        ? '공통 속성은 가리킬 타입을 정해야 합니다 — 구현 타입마다 다른 것을 가리키면 같은 속성이 아닙니다.'
                        : '안 정하면 아무 객체나 선택할 수 있습니다 — 선택할 것이 많아지면 사람은 못 찾고, 못 찾으면 없는 줄 알고 새로 만듭니다.'}
                      {interfaces.some((one) => one.slug === refType) &&
                        ' 인터페이스를 고르면 그것을 구현한 타입 전부의 객체에서 고릅니다 — 구현 타입이 늘어도 여기는 그대로입니다.'}
                    </p>
                  </div>
                )}

                {dataType === 'object_ref' && !isInterface && (
                  <div className="space-y-1.5">
                    <Label htmlFor="prop-inverse">상대 쪽에서 읽는 말</Label>
                    <Input
                      id="prop-inverse"
                      value={inverseLabel}
                      placeholder="예: 「과제」 칸이면 과제 쪽에서는 「개발모델」"
                      onChange={(event) => setInverseLabel(event.target.value)}
                    />
                    <p className="text-muted-foreground text-xs">
                      참조 칸은 칸에 저장한 관계입니다 — 그래프와 「관련 객체」 가 상대 쪽에서는 이
                      말로 읽습니다. 비우면 이 타입의 이름으로 읽습니다.
                    </p>
                  </div>
                )}
              </section>

              <section className="space-y-4">
                <h3 className="text-muted-foreground border-b pb-1 text-xs font-semibold">
                  어떻게 담나
                </h3>
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <Label htmlFor="prop-unit">단위</Label>
                    <Input
                      id="prop-unit"
                      value={unit}
                      disabled={locked}
                      placeholder="mm"
                      onChange={(event) => setUnit(event.target.value)}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="prop-sort">순서</Label>
                    <Input
                      id="prop-sort"
                      type="number"
                      value={sortOrder}
                      onChange={(event) => setSortOrder(event.target.value)}
                    />
                  </div>
                </div>

                {NUMERIC.has(dataType) && (
                  <div className="space-y-1.5">
                    <Label>값의 범위</Label>
                    <div className="grid grid-cols-3 gap-3">
                      <Input
                        type="number"
                        disabled={locked}
                        placeholder="아래 끝"
                        value={minValue}
                        onChange={(event) => setMinValue(event.target.value)}
                      />
                      <Input
                        type="number"
                        disabled={locked}
                        placeholder="위 끝"
                        value={maxValue}
                        onChange={(event) => setMaxValue(event.target.value)}
                      />
                      <Input
                        type="number"
                        disabled={locked}
                        placeholder="소수 자릿수"
                        value={decimals}
                        onChange={(event) => setDecimals(event.target.value)}
                      />
                    </div>
                    <p className="text-muted-foreground text-xs">
                      비우면 안 봅니다 — <b>그러면 두께가 -5mm 여도 통과합니다.</b> 소수 자릿수를
                      넘는 값은 <b>반올림하지 않고 거절</b>합니다(조용히 바꾸면 입력한 값과 저장된
                      값이 달라집니다).
                    </p>
                  </div>
                )}

                {PATTERNABLE.has(dataType) && (
                  <div className="space-y-1.5">
                    <Label htmlFor="prop-pattern">모양 규칙</Label>
                    <Input
                      id="prop-pattern"
                      value={pattern}
                      disabled={locked}
                      placeholder="^D-\\d{4}$"
                      className="font-mono"
                      onChange={(event) => setPattern(event.target.value)}
                    />
                    <p className="text-muted-foreground text-xs">
                      사번·도번처럼 <b>모양이 정해진 값</b>에 사용합니다(정규식). 비우면 안 봅니다.
                    </p>
                  </div>
                )}

                {!isInterface && (
                  <div className="space-y-1.5">
                    <Label htmlFor="prop-default">기본값</Label>
                    <Input
                      id="prop-default"
                      value={defaultValue}
                      onChange={(event) => setDefaultValue(event.target.value)}
                    />
                    <p className="text-muted-foreground text-xs">
                      <b>만들 때만</b> 채웁니다. 수정할 때도 채우면 사람이 방금 삭제한 값이
                      되살아나고, 그 되살아남은 저장한 사람 눈에 안 보입니다.
                    </p>
                  </div>
                )}

                <div className="space-y-1.5">
                  <Label htmlFor="prop-help">안내</Label>
                  <Input
                    id="prop-help"
                    value={help}
                    placeholder="칸 아래에 뜨는 한 줄. 무엇을 넣는 자리인지 적습니다."
                    onChange={(event) => setHelp(event.target.value)}
                  />
                </div>
              </section>

              {/* 규칙 셋은 설명이 길다 — 단 안에 두면 오른쪽만 길어진다. 두 단 아래 전폭으로. */}
              <div className="space-y-2 border-t pt-3 xl:col-span-2">
                <label className="flex items-start gap-2 text-sm">
                  <input
                    type="checkbox"
                    className="mt-0.5 size-4"
                    checked={required}
                    onChange={(event) => setRequired(event.target.checked)}
                  />
                  <span>
                    필수
                    <span className="text-muted-foreground ml-1 text-xs">
                      비어 있으면 저장이 거절됩니다. <b>이미 있는 객체는 그대로 둡니다</b> — 그
                      객체의 <b>속성을 수정할 때</b> 걸립니다(이름만 수정하는 것은 막지 않습니다).
                    </span>
                  </span>
                </label>
                {dataType === 'file' && (
                  <label className="flex items-start gap-2 text-sm">
                    <input
                      type="checkbox"
                      className="mt-0.5 size-4"
                      checked={imageOnly}
                      onChange={(event) => setImageOnly(event.target.checked)}
                    />
                    <span>
                      사진만 허용
                      <span className="text-muted-foreground ml-1 text-xs">
                        PNG · JPEG · GIF · WebP 만 업로드됩니다 — <b>서버가 파일을 열어</b>
                        확인합니다(이름 · 확장자로 판단하지 않습니다). 상세 화면에 사진 격자로
                        표시됩니다. 끄면 모든 파일을 업로드할 수 있고, 사진은 그대로 미리보기로
                        표시됩니다.
                      </span>
                    </span>
                  </label>
                )}
                {!isInterface && UNIQUEABLE.has(dataType) && (
                  <label className="flex items-start gap-2 text-sm">
                    <input
                      type="checkbox"
                      className="mt-0.5 size-4"
                      checked={unique}
                      onChange={(event) => setUnique(event.target.checked)}
                    />
                    <span>
                      유일해야 함
                      <span className="text-muted-foreground ml-1 text-xs">
                        시리얼·사번처럼 겹치면 안 되는 값. 범위는 타입의 <b>식별자 범위</b>를
                        따릅니다. <b>같은 것이 둘이 되면 둘 다 못 믿게 됩니다.</b>
                      </span>
                    </span>
                  </label>
                )}

                <label className="flex items-start gap-2 text-sm">
                  <input
                    type="checkbox"
                    className="mt-0.5 size-4"
                    checked={multi}
                    disabled={locked}
                    onChange={(event) => setMulti(event.target.checked)}
                  />
                  <span>
                    여러 값
                    <span className="text-muted-foreground ml-1 text-xs">
                      목록으로 저장합니다. <b>이미 값이 있는 속성에서 켜고 끄면</b> 그 값들이 새
                      모양에 안 맞아 수정할 때 거절됩니다.
                    </span>
                  </span>
                </label>
              </div>
            </div>
          </div>

          <DialogFooter className="justify-between sm:justify-between">
            {editing && locked ? (
              <span className="text-muted-foreground text-xs">
                공통 속성은 여기서 삭제하지 않습니다 — 구현을 해제하면 이 타입의 속성이 됩니다.
              </span>
            ) : editing ? (
              <Button
                variant="ghost"
                disabled={saving}
                onClick={async () => {
                  // **삭제 전에 몇 개가 안 보이게 되는지 먼저 읽는다.**
                  const found = await calls.usage(property!.key)
                  setUsage(found.objects_with_value)
                  setOpened(found.core_open ? found.core_consumers : null)
                  setRemoving(true)
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
              <Button onClick={save} disabled={saving || !key.trim() || !label.trim()}>
                {editing ? '저장' : '추가'}
              </Button>
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {retyping && property && (
        <RetypeDialog
          owner={owner}
          property={property}
          types={types}
          interfaces={interfaces}
          onClose={() => setRetyping(false)}
          onDone={() => {
            // 이 창이 들고 있는 정의는 옛 종류다 — 「저장」 을 누르면 옛 종류를 다시 보낸다.
            onChanged()
            onClose()
          }}
        />
      )}

      {removing && property && (
        <ConfirmDialog
          open
          destructive
          title={`${property.label} ${isInterface ? '공통 속성' : '속성'}을 삭제합니다`}
          description={
            <div className="space-y-2">
              {isInterface ? (
                <p>
                  구현 타입들에서 이 값을 가진 객체가 <b>{usage ?? 0}개</b> 있습니다. 공통 속성에서
                  빼도 <b>구현 타입의 속성은 남습니다</b> — 그 타입의 것이 되어, 그 뒤로는 타입에서
                  수정합니다.
                </p>
              ) : (
                <p>
                  지금 이 값을 가진 객체가 <b>{usage ?? 0}개</b> 있습니다. 정의를 삭제하면 그 값들은{' '}
                  <b>화면에서 사라집니다</b> — 데이터는 남아 있어, 같은 키로 다시 정의하면 도로
                  보입니다.
                </p>
              )}
              {/* **바깥에 연 타입이면 약속을 깨는 일이다.** 그쪽 코드에 이 칸 이름이 박혀
                  있으므로, 누가 읽을 수 있는지 이름을 들어 보여 준다. */}
              {opened && (
                <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-2">
                  <p className="font-medium">이 타입은 바깥에 열려 있습니다(코어).</p>
                  <p className="text-muted-foreground mt-1">
                    지우면 받아 가는 쪽에서 <b>이 칸이 조용히 사라집니다.</b> 읽을 수 있는 자격{' '}
                    {opened.length}개:
                  </p>
                  <ul className="text-muted-foreground mt-1 list-inside list-disc">
                    {opened.map((one) => (
                      <li key={one}>{one}</li>
                    ))}
                  </ul>
                  <p className="mt-1">쓰는 쪽에 알린 뒤 진행하세요.</p>
                </div>
              )}
            </div>
          }
          confirmLabel="삭제"
          onConfirm={async () => {
            await calls.remove(property.key, opened !== null)
            onChanged()
            onClose()
          }}
          onClose={() => {
            setRemoving(false)
            setUsage(null)
            setOpened(null)
          }}
        />
      )}
    </>
  )
}

/** 기본값을 그 종류의 모양으로. **글로 저장하면 숫자 속성이 사전순으로 정렬된다.** */
/** 기본값 칸의 글자 → 저장할 값. **여러 값 칸은 목록**으로 — 쉼표로 나눈다. 글자 하나로 보내면
 * 「A, B」 가 값 하나가 되어 저장이 거절됐다(2026-10-08). */
function coerceDefault(kind: DataType, raw: string, multi = false): unknown {
  if (multi) {
    return raw
      .split(',')
      .map((one) => one.trim())
      .filter(Boolean)
      .map((one) => coerceDefault(kind, one))
  }
  if (kind === 'number') return Number(raw)
  if (kind === 'bool') return raw === 'true' || raw === '예'
  return raw
}
