/**
 * 디지털 트윈 › 인프라 — **사람 아닌 조건**(S/W · 계산 자원 · 물성).
 *
 * ⚠️ **공유 자원은 전사 합계에서 한 번만 센다.** 부서마다 적힌 공유 라이선스를 그대로
 *    더하면 전사 합이 실제보다 커지고, 그 숫자로 투자를 판단하면 이미 있는 것을 또 산다.
 *    줄마다 「전사 공유」 를 표시하고, 합계는 서버가 그 표시로 걸러 센다.
 */

import { Plus, Save, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'

import { workspaceApi } from '@/modules/workspaces/api'
import { useAuth } from '@/shared/auth/AuthContext'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/components/ui/select'
import { useResource } from '@/shared/hooks/useResource'

import { dtApi, type HwRow, type SwRow } from './api'

const UNITS = [
  { key: 'copy', label: '카피' },
  { key: 'token', label: '토큰' },
  { key: 'unit', label: '대' },
]
const PURPOSES = [
  { key: 'solve', label: '해석' },
  { key: 'parallel', label: '병렬' },
  { key: 'prepost', label: '전후처리' },
]

export default function InfraPage() {
  const { user } = useAuth()
  const home = user?.home_workspace_slug ?? user?.memberships[0]?.slug ?? ''
  const workspaces = useResource(() => workspaceApi.list(true), [])
  const [target, setTarget] = useState(home)
  const current = useResource(() => (target ? dtApi.capacity(target) : Promise.resolve(null)), [
    target,
  ])
  const summary = useResource(() => dtApi.capacitySummary(), [])

  const [sw, setSw] = useState<SwRow[]>([])
  const [hw, setHw] = useState<HwRow[]>([])
  const [materials, setMaterials] = useState('')
  const [std, setStd] = useState(false)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState<Error | null>(null)

  // 부서를 바꾸면 그 부서의 값으로 칸을 채운다.
  useEffect(() => {
    const row = current.data
    if (!row) return
    setSw(row.sw)
    setHw(row.hw)
    setMaterials(row.material_types === null ? '' : String(row.material_types))
    setStd(row.has_process_std)
    setNote(row.note)
  }, [current.data])

  async function save() {
    if (!target) return
    setBusy(true)
    setFailed(null)
    try {
      await dtApi.capacitySave(target, {
        sw,
        hw,
        material_types: materials === '' ? null : Number(materials),
        has_process_std: std,
        note,
      })
      current.reload()
      summary.reload()
    } catch (caught) {
      setFailed(caught as Error)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="인프라"
        description="S/W 라이선스 · 계산 자원 · 물성입니다. 전사 공유 자원은 전사 합계에서 1회만 집계합니다."
      />

      <ErrorNotice error={failed ?? current.error ?? summary.error} />

      {/* 전사 합계 — 공유를 한 번만 센 값이다. */}
      <section className="grid gap-3 sm:grid-cols-4">
        <div className="rounded-md border p-3">
          <p className="text-muted-foreground text-xs">적은 부서</p>
          <p className="text-2xl font-semibold tabular-nums">{summary.data?.departments ?? 0}</p>
        </div>
        <div className="rounded-md border p-3">
          <p className="text-muted-foreground text-xs">CPU 코어 (전사)</p>
          <p className="text-2xl font-semibold tabular-nums">
            {summary.data?.hw.cpu_cores ?? 0}
          </p>
        </div>
        <div className="rounded-md border p-3">
          <p className="text-muted-foreground text-xs">GPU 보유 자원</p>
          <p className="text-2xl font-semibold tabular-nums">{summary.data?.hw.gpu_units ?? 0}</p>
          <p className="text-muted-foreground text-xs">사양은 글로 적습니다 — 개수를 더하지 않습니다</p>
        </div>
        <div className="rounded-md border p-3">
          <p className="text-muted-foreground text-xs">물성 종수 · 공정 표준</p>
          <p className="text-2xl font-semibold tabular-nums">
            {summary.data?.material_types ?? 0}
            <span className="text-muted-foreground ml-2 text-sm font-normal">
              · {summary.data?.process_std ?? 0}개 부서
            </span>
          </p>
        </div>
      </section>

      {(summary.data?.sw ?? []).length > 0 && (
        <section className="space-y-2">
          <h2 className="text-base font-semibold">전사 S/W 라이선스</h2>
          <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {(summary.data?.sw ?? []).map((one) => (
              <li key={one.name} className="flex items-baseline gap-2 rounded-md border p-2 text-sm">
                <span className="min-w-0 flex-1 truncate">{one.name}</span>
                <span className="tabular-nums">{one.quantity}</span>
              </li>
            ))}
          </ul>
          <p className="text-muted-foreground text-xs">
            「전사 공유」 로 표시한 자원은 여러 부서가 적어도 한 번만 셉니다.
          </p>
        </section>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <span className="text-muted-foreground text-sm">부서</span>
        <Select value={target} onValueChange={setTarget}>
          <SelectTrigger className="w-56">
            <SelectValue placeholder="부서 선택" />
          </SelectTrigger>
          <SelectContent>
            {(workspaces.data ?? []).map((one) => (
              <SelectItem key={one.slug} value={one.slug}>
                {one.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button className="ml-auto" onClick={() => void save()} disabled={busy || !target}>
          <Save className="size-4" /> 저장
        </Button>
      </div>

      <section className="space-y-2 rounded-md border p-4">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold">S/W 라이선스</h2>
          <Button
            variant="outline"
            size="sm"
            className="ml-auto"
            onClick={() => setSw((was) => [...was, { name: '', quantity: 1, unit: 'copy' }])}
          >
            <Plus className="size-4" /> 줄 추가
          </Button>
        </div>
        {sw.length === 0 ? (
          <p className="text-muted-foreground text-sm">적은 라이선스가 없습니다.</p>
        ) : (
          <ul className="space-y-2">
            {sw.map((row, index) => (
              <li key={index} className="grid gap-2 sm:grid-cols-[1fr_6rem_7rem_8rem_auto]">
                <Input
                  value={row.name}
                  placeholder="툴 이름"
                  onChange={(event) =>
                    setSw((was) =>
                      was.map((one, at) => (at === index ? { ...one, name: event.target.value } : one)),
                    )
                  }
                />
                <Input
                  type="number"
                  value={row.quantity}
                  onChange={(event) =>
                    setSw((was) =>
                      was.map((one, at) =>
                        at === index ? { ...one, quantity: Number(event.target.value) } : one,
                      ),
                    )
                  }
                />
                <Select
                  value={row.unit ?? 'copy'}
                  onValueChange={(value) =>
                    setSw((was) => was.map((one, at) => (at === index ? { ...one, unit: value } : one)))
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {UNITS.map((one) => (
                      <SelectItem key={one.key} value={one.key}>
                        {one.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Select
                  value={row.purpose ?? 'solve'}
                  onValueChange={(value) =>
                    setSw((was) =>
                      was.map((one, at) => (at === index ? { ...one, purpose: value } : one)),
                    )
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {PURPOSES.map((one) => (
                      <SelectItem key={one.key} value={one.key}>
                        {one.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <div className="flex items-center gap-2">
                  <label className="flex items-center gap-1 text-xs whitespace-nowrap">
                    <input
                      type="checkbox"
                      checked={Boolean(row.shared)}
                      onChange={(event) =>
                        setSw((was) =>
                          was.map((one, at) =>
                            at === index ? { ...one, shared: event.target.checked } : one,
                          ),
                        )
                      }
                    />
                    전사 공유
                  </label>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setSw((was) => was.filter((_, at) => at !== index))}
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="space-y-2 rounded-md border p-4">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold">계산 자원</h2>
          <Button
            variant="outline"
            size="sm"
            className="ml-auto"
            onClick={() => setHw((was) => [...was, { name: '', cpu_cores: 0, ram_gb: 0, gpu: '' }])}
          >
            <Plus className="size-4" /> 줄 추가
          </Button>
        </div>
        {hw.length === 0 ? (
          <p className="text-muted-foreground text-sm">적은 자원이 없습니다.</p>
        ) : (
          <ul className="space-y-2">
            {hw.map((row, index) => (
              <li key={index} className="grid gap-2 sm:grid-cols-[1fr_6rem_6rem_8rem_auto]">
                <Input
                  value={row.name}
                  placeholder="자원 이름"
                  onChange={(event) =>
                    setHw((was) =>
                      was.map((one, at) => (at === index ? { ...one, name: event.target.value } : one)),
                    )
                  }
                />
                {(['cpu_cores', 'ram_gb'] as const).map((field) => (
                  <Input
                    key={field}
                    type="number"
                    value={row[field] ?? 0}
                    placeholder={field === 'cpu_cores' ? '코어' : 'RAM GB'}
                    onChange={(event) =>
                      setHw((was) =>
                        was.map((one, at) =>
                          at === index ? { ...one, [field]: Number(event.target.value) } : one,
                        ),
                      )
                    }
                  />
                ))}
                {/* **GPU 는 글이다.** 「A100 4장」 처럼 적는 값이라 숫자 칸으로 두면
                    적을 수 있는 것을 못 적게 만든다. */}
                <Input
                  value={row.gpu ?? ''}
                  placeholder="GPU 사양"
                  onChange={(event) =>
                    setHw((was) =>
                      was.map((one, at) => (at === index ? { ...one, gpu: event.target.value } : one)),
                    )
                  }
                />
                <div className="flex items-center gap-2">
                  <label className="flex items-center gap-1 text-xs whitespace-nowrap">
                    <input
                      type="checkbox"
                      checked={Boolean(row.shared)}
                      onChange={(event) =>
                        setHw((was) =>
                          was.map((one, at) =>
                            at === index ? { ...one, shared: event.target.checked } : one,
                          ),
                        )
                      }
                    />
                    전사 공유
                  </label>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setHw((was) => was.filter((_, at) => at !== index))}
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="grid gap-3 rounded-md border p-4 sm:grid-cols-3">
        <div className="space-y-1">
          <span className="text-muted-foreground text-xs">물성 종수</span>
          <Input
            type="number"
            value={materials}
            onChange={(event) => setMaterials(event.target.value)}
          />
        </div>
        <label className="flex items-center gap-2 self-end text-sm">
          <input type="checkbox" checked={std} onChange={(event) => setStd(event.target.checked)} />
          해석 공정 표준을 보유
        </label>
        <div className="space-y-1">
          <span className="text-muted-foreground text-xs">메모</span>
          <Input value={note} onChange={(event) => setNote(event.target.value)} />
        </div>
      </section>
    </div>
  )
}
