/**
 * 속성 정의 하나 — 생성과 수정을 **같은 창이 한다.**
 *
 * 두 벌로 만들면 칸이 갈리고, 갈린 것은 한쪽만 고쳐진다. 그러면 「만들 때는
 * 정할 수 있는데 고칠 때는 없는 칸」 이 생기고, 그것을 고치려면 지웠다 다시
 * 만들어야 한다 — 그 순간 그 속성의 값이 전부 화면에서 사라진다.
 *
 * **키와 종류는 만들 때만 정한다.** 키를 바꾸면 이미 저장된 값이 전부 고아가
 * 되고, 종류를 바꾸면 그 값들이 새 종류에 안 맞는데 화면은 아무 말도 안 한다.
 */

import { useState } from 'react'

import { ontologyApi } from '@/modules/ontology/api'
import { EnumOptionsPanel } from '@/modules/ontology/EnumOptionsPanel'
import type { DataType, ObjectType, PropertyDef } from '@/modules/ontology/api'
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
  SelectItem,
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
  date: 'YYYY-MM-DD. 날짜 선택기가 뜹니다.',
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

interface Props {
  /** 이 속성이 붙는 타입. */
  type: ObjectType
  /** 고칠 속성. 없으면 생성다. */
  property?: PropertyDef | null
  /** 「객체 참조」 가 가리킬 수 있는 타입들. */
  types: ObjectType[]
  onClose: () => void
  onChanged: () => void
}

export function PropertyEditDialog({ type, property, types, onClose, onChanged }: Props) {
  const editing = Boolean(property)

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
  const [defaultValue, setDefaultValue] = useState(
    property?.default_value == null ? '' : String(property.default_value),
  )
  const [unique, setUnique] = useState(property?.unique ?? false)
  const [refType, setRefType] = useState(property?.ref_type_slug ?? '')
  const [inverseLabel, setInverseLabel] = useState(property?.inverse_label ?? '')

  const [error, setError] = useState<Error | null>(null)
  const [saving, setSaving] = useState(false)
  const [removing, setRemoving] = useState(false)
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
      min_value: NUMERIC.has(dataType) && minValue !== '' ? Number(minValue) : null,
      max_value: NUMERIC.has(dataType) && maxValue !== '' ? Number(maxValue) : null,
      decimals: NUMERIC.has(dataType) && decimals !== '' ? Number(decimals) : null,
      pattern: PATTERNABLE.has(dataType) && pattern ? pattern : null,
      // **빈 칸은 「기본값 없음」 이다.** 빈 문자열을 넣으면 그것이 기본값이 되고,
      // 그러면 필수 검사가 통과해 버린다.
      default_value: defaultValue === '' ? null : coerceDefault(dataType, defaultValue),
      unique: UNIQUEABLE.has(dataType) ? unique : false,
      enum_options:
        dataType === 'enum'
          ? options
              .split(',')
              .map((one) => one.trim())
              .filter(Boolean)
          : null,
      ref_type_slug: dataType === 'object_ref' ? refType || null : null,
      inverse_label: dataType === 'object_ref' ? inverseLabel.trim() : '',
    }
  }

  async function save() {
    setError(null)
    setSaving(true)
    try {
      if (property) await ontologyApi.updateProperty(type.slug, property.key, body())
      else await ontologyApi.createProperty(type.slug, body())
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
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>
              {type.label} · {editing ? `${property?.label} 수정` : '속성 추가'}
            </DialogTitle>
          </DialogHeader>

          <div className="space-y-4">
            {error && <ErrorNotice error={error} />}

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
              {editing ? (
                <>
                  <Input value={DATA_TYPE_LABELS[dataType]} readOnly disabled />
                  <p className="text-muted-foreground text-xs">
                    <b>종류는 바꿀 수 없습니다.</b> 이미 저장된 값이 새 종류에 안 맞아도 화면이
                    그것을 말해 주지 못합니다 — 바꾸려면 새 속성을 만들어 옮기세요.
                  </p>
                </>
              ) : (
                <>
                  <Select value={dataType} onValueChange={(next) => setDataType(next as DataType)}>
                    <SelectTrigger id="prop-type">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {(Object.keys(DATA_TYPE_LABELS) as DataType[]).map((one) => (
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
                <Label htmlFor="prop-options">고를 값 (쉼표로)</Label>
                <Input
                  id="prop-options"
                  value={options}
                  placeholder="A, B, C"
                  onChange={(event) => setOptions(event.target.value)}
                />
                <p className="text-muted-foreground text-xs">
                  이미 쓰이는 값을 목록에서 빼면 <b>그 값을 가진 객체는 고칠 때 거절됩니다.</b>
                  빼기 전에 그 값을 쓰는 것이 있는지 보세요.
                </p>
              </div>
            )}

            {/* 저장된 속성의 고를 값 — 이름을 바꾸면 저장값도 함께, 코드표로 승격. */}
            {editing && property && property.data_type === 'enum' && dataType === 'enum' && (
              <EnumOptionsPanel
                type={type}
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
                    setDefaultValue((current) => (current === renamed.from ? renamed.to : current))
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
                <Select value={refType} onValueChange={setRefType}>
                  <SelectTrigger id="prop-ref">
                    <SelectValue placeholder="아무 타입이나" />
                  </SelectTrigger>
                  <SelectContent>
                    {types.map((one) => (
                      <SelectItem key={one.slug} value={one.slug}>
                        {one.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-muted-foreground text-xs">
                  안 정하면 아무 객체나 고를 수 있습니다 — 고를 것이 많아지면 사람은 못 찾고, 못
                  찾으면 없는 줄 알고 새로 만듭니다.
                </p>
                <Label htmlFor="prop-inverse">상대 쪽에서 읽는 말</Label>
                <Input
                  id="prop-inverse"
                  value={inverseLabel}
                  placeholder="예: 「과제」 칸이면 과제 쪽에서는 「개발모델」"
                  onChange={(event) => setInverseLabel(event.target.value)}
                />
                <p className="text-muted-foreground text-xs">
                  참조 칸은 칸에 저장한 관계입니다 — 그래프와 「관련 객체」 가 상대 쪽에서는 이 말로
                  읽습니다. 비우면 이 타입의 이름으로 읽습니다.
                </p>
              </div>
            )}

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="prop-unit">단위</Label>
                <Input
                  id="prop-unit"
                  value={unit}
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
                    placeholder="아래 끝"
                    value={minValue}
                    onChange={(event) => setMinValue(event.target.value)}
                  />
                  <Input
                    type="number"
                    placeholder="위 끝"
                    value={maxValue}
                    onChange={(event) => setMaxValue(event.target.value)}
                  />
                  <Input
                    type="number"
                    placeholder="소수 자릿수"
                    value={decimals}
                    onChange={(event) => setDecimals(event.target.value)}
                  />
                </div>
                <p className="text-muted-foreground text-xs">
                  비우면 안 봅니다 — <b>그러면 두께가 -5mm 여도 통과합니다.</b> 소수 자릿수를 넘는
                  값은 <b>반올림하지 않고 거절</b>합니다(조용히 바꾸면 넣은 값과 저장된 값이
                  달라집니다).
                </p>
              </div>
            )}

            {PATTERNABLE.has(dataType) && (
              <div className="space-y-1.5">
                <Label htmlFor="prop-pattern">모양 규칙</Label>
                <Input
                  id="prop-pattern"
                  value={pattern}
                  placeholder="^D-\\d{4}$"
                  className="font-mono"
                  onChange={(event) => setPattern(event.target.value)}
                />
                <p className="text-muted-foreground text-xs">
                  사번·도번처럼 <b>모양이 정해진 값</b>에 씁니다(정규식). 비우면 안 봅니다.
                </p>
              </div>
            )}

            <div className="space-y-1.5">
              <Label htmlFor="prop-default">기본값</Label>
              <Input
                id="prop-default"
                value={defaultValue}
                onChange={(event) => setDefaultValue(event.target.value)}
              />
              <p className="text-muted-foreground text-xs">
                <b>만들 때만</b> 채웁니다. 고칠 때도 채우면 사람이 방금 지운 값이 되살아나고, 그
                되살아남은 저장한 사람 눈에 안 보입니다.
              </p>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="prop-help">안내</Label>
              <Input
                id="prop-help"
                value={help}
                placeholder="칸 아래에 뜨는 한 줄. 무엇을 넣는 자리인지 적습니다."
                onChange={(event) => setHelp(event.target.value)}
              />
            </div>

            <div className="space-y-2">
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
                    비어 있으면 저장이 거절됩니다. <b>이미 있는 객체는 그대로 둡니다</b> — 그 객체의{' '}
                    <b>속성을 고칠 때</b> 걸립니다(이름만 고치는 것은 막지 않습니다).
                  </span>
                </span>
              </label>
              {UNIQUEABLE.has(dataType) && (
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
                  onChange={(event) => setMulti(event.target.checked)}
                />
                <span>
                  여러 값
                  <span className="text-muted-foreground ml-1 text-xs">
                    목록으로 저장합니다. <b>이미 값이 있는 속성에서 켜고 끄면</b> 그 값들이 새
                    모양에 안 맞아 고칠 때 거절됩니다.
                  </span>
                </span>
              </label>
            </div>
          </div>

          <DialogFooter className="justify-between sm:justify-between">
            {editing ? (
              <Button
                variant="ghost"
                disabled={saving}
                onClick={async () => {
                  // **삭제 전에 몇 개가 안 보이게 되는지 먼저 읽는다.**
                  const found = await ontologyApi.propertyUsage(type.slug, property!.key)
                  setUsage(found.objects_with_value)
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

      {removing && property && (
        <ConfirmDialog
          open
          destructive
          title={`${property.label} 속성을 지웁니다`}
          description={
            <>
              지금 이 값을 가진 객체가 <b>{usage ?? 0}개</b> 있습니다. 정의를 지우면 그 값들은{' '}
              <b>화면에서 사라집니다</b> — 데이터는 남아 있어, 같은 키로 다시 정의하면 도로
              보입니다.
            </>
          }
          confirmLabel="삭제"
          onConfirm={async () => {
            await ontologyApi.removeProperty(type.slug, property.key)
            onChanged()
            onClose()
          }}
          onClose={() => {
            setRemoving(false)
            setUsage(null)
          }}
        />
      )}
    </>
  )
}

/** 기본값을 그 종류의 모양으로. **글로 저장하면 숫자 속성이 사전순으로 정렬된다.** */
function coerceDefault(kind: DataType, raw: string): unknown {
  if (kind === 'number') return Number(raw)
  if (kind === 'bool') return raw === 'true' || raw === '예'
  return raw
}
