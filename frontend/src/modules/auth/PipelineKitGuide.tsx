/**
 * **정제 도구 키트** — 원천 파일을 정제해 한꺼번에(수만 줄) 넣는 길. 사용자 PC 에 푼다.
 *
 * 서버가 같은 판의 zip 을 들고 있다가 여기서 내려준다. 따로(GitHub 릴리스에서) 받게 두었더니
 * 사내망에서는 받을 길이 없었고, 운영의 사람도 AI 도 그런 것이 있는 줄 몰랐다(실측).
 *
 * 받은 뒤 할 일은 **명령 한 줄**이다(`sp_setup.py`) — 이 설치의 주소와 방금 받은 토큰을 채워서
 * 보여 준다. 사람은 「서버 주소가 뭐였지 · 토큰을 어디에 넣지」 에서 막힌다.
 */

import { useState } from 'react'
import { Download } from 'lucide-react'

import { Snippet } from '@/modules/auth/McpSetupGuide'
import { api, downloadFile } from '@/shared/api/client'
import { PUBLIC_PATH } from '@/shared/base'
import { APP_SLUG } from '@/shared/branding'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Button } from '@/shared/components/ui/button'
import { useResource } from '@/shared/hooks/useResource'

const TOKEN_PLACEHOLDER = '‹발급받은_토큰›'
/** 작업 폴더의 예 — 원천 · 정의 · 대응이 쌓이는 곳(저장소 밖, 사내 PC). */
const WORK_ROOT = 'D:\\온톨로지작업'

export interface PipelineKit {
  available: boolean
  filename: string
  version: string
  size_bytes: number
}

/** 정제 도구가 부를 이 설치의 주소 — 접두어(`/<slug>`)까지. 도구가 뒤에 `/api/…` 를 붙인다. */
export function appUrlFrom(origin: string, prefix: string = PUBLIC_PATH): string {
  return `${origin}${prefix}`
}

/**
 * 설치 명령 — **한 줄로.** 줄 잇기 기호가 셸마다 다르다(cmd 는 `^`, PowerShell 은 `` ` ``) —
 * 하나를 고르면 다른 쪽에서 깨진 명령이 되고, 사람은 그것을 키트가 고장 난 것으로 읽는다.
 */
export function setupCommand(
  token: string | null,
  server: string,
  platform: string = APP_SLUG,
): string {
  return (
    `python sp_setup.py --work-root "${WORK_ROOT}" --platform ${platform} ` +
    `--server ${server} --token ${token || TOKEN_PLACEHOLDER} --write-claude`
  )
}

function megabytes(bytes: number): string {
  return `${(bytes / 1024 / 1024).toFixed(0)}MB`
}

export function PipelineKitGuide({ token }: { token: string | null }) {
  const kit = useResource(() => api.get<PipelineKit>('/server/pipeline-kit/info'), [])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const server = appUrlFrom(typeof window === 'undefined' ? '' : window.location.origin)

  async function download(filename: string) {
    setBusy(true)
    setError(null)
    try {
      await downloadFile('/server/pipeline-kit', filename)
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="space-y-3 rounded-md border p-4">
      <div>
        <h3 className="text-sm font-semibold">정제 도구 키트 — 원천 파일을 한꺼번에 넣을 때</h3>
        <p className="text-muted-foreground mt-1 text-xs">
          엑셀 · CSV 같은 원천을 AI 와 함께 정리해 <b>수만 줄을 한 번에</b> 넣는 도구입니다. 서버가
          아니라 <b>내 PC</b> 에 풀어 씁니다 — 원천 파일이 PC 에 있고, 그 내용이 대화를 거치지 않고
          서버로 곧장 갑니다. 위의 「AI 도구 연결」 과 함께 붙여 둡니다.
        </p>
      </div>

      {kit.error && <ErrorNotice error={kit.error} />}
      {error && <ErrorNotice error={error} />}

      {kit.data && !kit.data.available && (
        <p className="text-muted-foreground rounded-md border border-dashed px-3 py-2 text-xs">
          이 설치에는 키트가 들어 있지 않습니다 — 서버 번들로 깐 설치에서 받을 수 있습니다.
        </p>
      )}

      {kit.data?.available && (
        <ol className="list-decimal space-y-3 pl-5 text-xs">
          <li className="space-y-1.5">
            <p>받습니다 — 이 서버와 같은 판입니다.</p>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => download(kit.data!.filename)}
            >
              <Download className="mr-1 size-3.5" />
              {kit.data.filename} ({megabytes(kit.data.size_bytes)})
            </Button>
          </li>
          <li>
            PC 에 풉니다(예: <span className="font-mono">D:\sp-pipeline</span>). 파이썬 3.12 이상이
            있어야 합니다 — 인터넷은 없어도 됩니다(필요한 것이 들어 있습니다).
          </li>
          <li className="space-y-1.5">
            <p>
              푼 폴더에서 명령 창을 열고 아래를 실행합니다.{' '}
              <span className="font-mono">--work-root</span> 는 원천 · 정의가 쌓일 폴더입니다.
              토큰에는 <span className="font-mono">read</span> ·{' '}
              <span className="font-mono">objects:write</span> 가 있어야 하고, 정의까지 넣으려면{' '}
              <span className="font-mono">ontology:write</span> 도 있어야 합니다. Gemini CLI 면 끝의{' '}
              <span className="font-mono">--write-claude</span> 를{' '}
              <span className="font-mono">--write-gemini</span> 로.
            </p>
            {!token && (
              <p className="text-muted-foreground">
                아직 토큰을 발급하지 않았습니다 — 토큰 자리에{' '}
                <span className="font-mono">{TOKEN_PLACEHOLDER}</span> 가 들어 있습니다.
              </p>
            )}
            <Snippet text={setupCommand(token, server)} label="명령" />
            <p className="text-muted-foreground">
              <b>플랫폼이 여럿이면</b> 각 플랫폼 화면의 이 명령을 같은 PC 에서 한 번씩 실행합니다 —
              앞에 등록한 것은 남고, 이 플랫폼은{' '}
              <span className="font-mono">{APP_SLUG}</span> 라는 이름으로 더해집니다. 작업을 만들
              때 어느 플랫폼에 넣을지 AI 가 묻습니다.
            </p>
          </li>
          <li>
            <b>Claude Desktop 을 완전히 종료했다가 다시 켭니다.</b> 그 뒤 대화에서 「
            <span className="font-mono">{WORK_ROOT}</span> 에 ○○ 자료 작업을 시작하자」 처럼
            말하면, AI 가 원천을 넣어 달라고 하는 것부터 차례로 안내합니다. 넣는 것(적용)은 늘
            사람이 미리 보기를 본 뒤에 합니다 — AI 가 알려 주는 적용 명령을 <b>아무 명령 창에나</b>{' '}
            붙여 넣으면 됩니다(설치가 이 서버 주소와 토큰을 키트 폴더에 기억해 둡니다).
          </li>
        </ol>
      )}
    </section>
  )
}
