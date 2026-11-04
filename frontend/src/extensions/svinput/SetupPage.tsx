/**
 * 설정 — 역할(문서 · 문제점 · 단계별 판정 …) → 허브의 타입 · 칸을 고른다(시스템 관리자).
 *
 * 후보는 서버가 준다(접두 `sv_pm_` 타입과 그 속성 키) — **없는 칸을 고를 수 없다.** 타입 slug 를
 * 화면에 박지 않는 까닭은 ADR 0051 에 있다.
 */

import { Save } from 'lucide-react'
import { useEffect, useState } from 'react'

import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/components/ui/select'
import { useResource } from '@/shared/hooks/useResource'

import { svApi, type RoleIn } from './api'

const NONE = '__none__'
/** 연결 칸 — 참조 칸 대신 관계 종류를 고를 수 있다(ADR 0051 7절). */
const LINK_FIELDS = new Set(['report_ref', 'issue_ref'])
const REL = 'rel:'

export default function SetupPage() {
  const status = useResource(() => svApi.status(), [])
  const candidates = useResource(() => svApi.candidates(), [])
  const [draft, setDraft] = useState<Record<string, RoleIn>>({})
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [failed, setFailed] = useState<Error | null>(null)

  // **현재값이 채워진 채로** 시작한다 — 빈 표를 주면 무엇을 적어야 하는지 알 수 없다.
  useEffect(() => {
    if (!status.data) return
    setDraft(
      Object.fromEntries(
        status.data.roles.map((one) => [one.key, { type: one.type_slug, fields: { ...one.fields } }]),
      ),
    )
  }, [status.data])

  if (status.error) return <ErrorNotice error={status.error} />
  if (candidates.error) return <ErrorNotice error={candidates.error} />
  if (!status.data || !candidates.data) return null

  const types = candidates.data.types
  /** 역할마다 고를 수 있는 타입 — 허브 역할은 접두가 맞는 것, 스파인은 그 밖(코어의 과제 · 모델). */
  const typesFor = (hub: boolean) => types.filter((one) => one.hub === hub)
  const relations = candidates.data.relations ?? []
  const propsOf = (slug: string | null) => types.find((one) => one.slug === slug)?.properties ?? []
  const stateOf = (key: string) => status.data?.roles.find((one) => one.key === key)

  function pickType(role: string, slug: string | null, defaults: Record<string, string>) {
    const keys = propsOf(slug)
    setDraft((prev) => ({
      ...prev,
      [role]: {
        type: slug,
        // 타입을 고르면 그 타입에 있는 기본 칸 이름을 미리 채운다 — 없는 칸은 비워 둔다.
        fields: Object.fromEntries(
          Object.entries(defaults).map(([field, key]) => [
            field,
            keys.includes(key) || key.startsWith(REL) ? key : null,
          ]),
        ),
      },
    }))
  }

  async function save() {
    setSaving(true)
    setFailed(null)
    setSaved(false)
    try {
      await svApi.saveSettings(draft)
      setSaved(true)
      status.reload()
    } catch (caught) {
      setFailed(caught as Error)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="자주검증 입력 › 설정"
        description={`역할마다 타입과 칸을 고릅니다. 허브 역할의 후보는 접두 「${candidates.data.prefix}」 타입(${typesFor(true).length}개), 스파인은 코어의 과제 · 모델 타입입니다.`}
        actions={
          <Button onClick={save} disabled={saving}>
            <Save className="size-4" />
            {saving ? '저장 중…' : '저장'}
          </Button>
        }
      />
      {failed && <ErrorNotice error={failed} />}
      {saved && <p className="text-sm text-emerald-700">저장했습니다.</p>}
      {typesFor(true).length === 0 && (
        <p className="text-muted-foreground text-sm">
          허브 타입이 없습니다 — 정의(`{candidates.data.prefix}*`)가 이 설치에 아직 없습니다.
        </p>
      )}
      <div className="space-y-4">
        {status.data.defs.map((def) => {
          const current = draft[def.key] ?? { type: null, fields: {} }
          const state = stateOf(def.key)
          const keys = propsOf(current.type)
          const options = typesFor(def.hub)
          return (
            <section key={def.key} className="space-y-3 rounded-md border p-4">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-sm font-semibold">{def.label}</h2>
                <span className="text-muted-foreground text-xs">{def.description}</span>
                {state?.missing.map((one) => (
                  <Badge key={one} variant="destructive">
                    없음: {one}
                  </Badge>
                ))}
              </div>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                <label className="space-y-1 text-sm">
                  <span className="text-muted-foreground text-xs">타입</span>
                  <Select
                    value={current.type ?? NONE}
                    onValueChange={(value) =>
                      pickType(
                        def.key,
                        value === NONE ? null : value,
                        Object.fromEntries(def.fields.map((f) => [f.key, f.default])),
                      )
                    }
                  >
                    <SelectTrigger aria-label={`${def.label} 타입`}>
                      <SelectValue placeholder="고르지 않음" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={NONE}>고르지 않음</SelectItem>
                      {options.map((one) => (
                        <SelectItem key={one.slug} value={one.slug}>
                          {one.label} ({one.slug})
                        </SelectItem>
                      ))}
                      {current.type && !options.some((one) => one.slug === current.type) && (
                        <SelectItem value={current.type}>{current.type}</SelectItem>
                      )}
                    </SelectContent>
                  </Select>
                </label>
                {def.fields.map((field) => (
                  <label key={field.key} className="space-y-1 text-sm">
                    <span className="text-muted-foreground text-xs">
                      {field.label}
                      {field.required ? ' *' : ''}
                    </span>
                    <Select
                      value={current.fields?.[field.key] ?? NONE}
                      disabled={!current.type}
                      onValueChange={(value) =>
                        setDraft((prev) => ({
                          ...prev,
                          [def.key]: {
                            ...current,
                            fields: { ...current.fields, [field.key]: value === NONE ? null : value },
                          },
                        }))
                      }
                    >
                      <SelectTrigger aria-label={`${def.label} ${field.label}`}>
                        <SelectValue placeholder="칸 고르기" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value={NONE}>비움</SelectItem>
                        {keys.map((key) => (
                          <SelectItem key={key} value={key}>
                            {key}
                          </SelectItem>
                        ))}
                        {LINK_FIELDS.has(field.key) &&
                          relations.map((rel) => (
                            <SelectItem key={REL + rel.slug} value={REL + rel.slug}>
                              관계: {rel.label} ({rel.slug})
                            </SelectItem>
                          ))}
                      </SelectContent>
                    </Select>
                  </label>
                ))}
              </div>
            </section>
          )
        })}
      </div>
    </div>
  )
}
