/**
 * 디지털 트윈 › 설정 — **고를 수 있는 값을 여기서 고친다**(시스템 관리자).
 *
 * 단위를 하나 더하는 일이 배포이면 그 목록은 결국 안 고쳐진 채로 쓰인다. 「대」 와 「코어」
 * 중 무엇으로 셀지는 이 설치의 사정이다.
 *
 * ⚠️ **키는 저장되는 값이다.** 이름은 언제든 고쳐도 되지만, 키를 고치는 것은 지우고 새로
 *    만드는 것과 같다 — 이미 적힌 줄이 그 키를 들고 있다.
 * ⚠️ **쓰이는 값은 지울 수 없다**(서버가 막는다). 지우면 그 줄의 값이 「모르는 값」 이 되고,
 *    그 줄을 고칠 사람은 왜 비었는지 모른다.
 */

import { RotateCcw, Save } from 'lucide-react'
import { useEffect, useState } from 'react'

import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { PasteGrid, type GridColumn } from '@/shared/components/PasteGrid'
import { Button } from '@/shared/components/ui/button'
import { useResource } from '@/shared/hooks/useResource'

import { dtApi, type Catalog } from './api'

const COLUMNS: GridColumn[] = [
  {
    key: 'key',
    header: 'key',
    label: '키',
    help: '저장되는 값 — 한 번 정하면 고치지 않습니다',
    required: true,
  },
  { key: 'label', header: 'label', label: '이름', help: '화면과 엑셀에 적히는 말', required: true },
  { key: 'in_use', header: '쓰이는 줄', help: '읽기용 — 쓰이는 값은 지울 수 없습니다', readOnly: true },
]

function rowsOf(one: Catalog): string[][] {
  return one.items.map((item) => [item.key, item.label, String(one.in_use[item.key] ?? 0)])
}

export default function SettingsPage() {
  const catalogs = useResource(() => dtApi.catalogs(), [])
  const [rows, setRows] = useState<Record<string, string[][]>>({})
  const [busy, setBusy] = useState<string | null>(null)
  const [failed, setFailed] = useState<Error | null>(null)
  const [saved, setSaved] = useState<string | null>(null)

  // **현재값이 채워진 채로** 시작한다 — 빈 표를 주면 무엇을 적어야 하는지 알 수 없다.
  useEffect(() => {
    if (!catalogs.data) return
    setRows(Object.fromEntries(catalogs.data.map((one) => [one.name, rowsOf(one)])))
  }, [catalogs.data])

  async function save(name: string, items: string[][]) {
    setBusy(name)
    setFailed(null)
    setSaved(null)
    try {
      const body = items
        .filter((row) => (row[0] ?? '').trim() || (row[1] ?? '').trim())
        .map((row) => ({ key: (row[0] ?? '').trim(), label: (row[1] ?? '').trim() }))
      await dtApi.catalogSave(name, body)
      setSaved(name)
      catalogs.reload()
    } catch (caught) {
      setFailed(caught as Error)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="설정"
        description="고를 수 있는 값을 고칩니다. 키는 저장되는 값이고 이름은 화면과 엑셀에 적히는 말입니다 — 쓰이는 값은 지울 수 없습니다."
      />

      <ErrorNotice error={failed ?? catalogs.error} />

      {(catalogs.data ?? []).map((one) => (
        <section key={one.name} className="space-y-2 rounded-md border p-4">
          <div className="flex flex-wrap items-baseline gap-2">
            <h2 className="text-sm font-semibold">{one.label}</h2>
            <span className="text-muted-foreground text-xs">{one.help}</span>
            {one.is_default && (
              <span className="text-muted-foreground text-xs">· 기본값입니다</span>
            )}
            {saved === one.name && <span className="text-xs text-emerald-600">· 저장했습니다</span>}
            <Button
              size="sm"
              variant="outline"
              className="ml-auto"
              disabled={busy !== null || one.is_default}
              // 기본값으로 — 빈 목록을 보내면 서버가 설정에서 지운다.
              onClick={() => void save(one.name, [])}
            >
              <RotateCcw className="size-4" /> 기본값으로
            </Button>
            <Button
              size="sm"
              disabled={busy !== null}
              onClick={() => void save(one.name, rows[one.name] ?? [])}
            >
              <Save className="size-4" /> 저장
            </Button>
          </div>
          <PasteGrid
            columns={COLUMNS}
            rows={rows[one.name] ?? []}
            onRows={(next) => setRows((was) => ({ ...was, [one.name]: next }))}
          />
        </section>
      ))}

      {(catalogs.data ?? []).length === 0 && !catalogs.error && (
        <p className="text-muted-foreground text-sm">고칠 목록이 없습니다.</p>
      )}
    </div>
  )
}
