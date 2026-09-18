/**
 * 발급한 토큰을 AI 도구에 넣는 법 — Claude Code · Claude Desktop · Codex CLI · Gemini CLI.
 *
 * 토큰을 발급한 자리에서 바로 보여 준다. 사람은 토큰을 받아 들고 「이걸 어디에 넣지」 에서 막히고,
 * 그때 README 를 찾아가는 사람은 드물다. 발급 전에도 예시 형식은 보여 주되 토큰 자리는 자리표시자.
 *
 * 네 도구 모두 **같은 다리(`npx mcp-remote`)** 로 붙인다(ReportArchive 와 같은 방식). 이 MCP 는
 * HTTP 서버인데 Claude Desktop · Gemini CLI 의 설정 파일은 stdio(command) 서버만 받고, http 주소
 * (`--allow-http`)와 사내 인증서(`NODE_OPTIONS=--use-system-ca`)를 한 방식으로 다루려는 뜻이다.
 * 토큰은 env 로 넣고 args 의 `${AUTH}` 는 mcp-remote 가 치환한다 — 셸이 먼저 풀지 않게 작은따옴표.
 */

import { useState } from 'react'

import { Button } from '@/shared/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/shared/components/ui/tabs'
import { APP_SLUG } from '@/shared/branding'
import { PUBLIC_PATH } from '@/shared/base'
import { copyText } from '@/shared/lib/clipboard'

const TOKEN_PLACEHOLDER = '‹발급받은_토큰›'

/**
 * 이 설치의 MCP 주소.
 * - 메인 서버(접두어) 뒤: `https://<호스트>/<slug>/mcp` — 조각이 `/<slug>/mcp` 를 MCP 로 넘긴다.
 * - 직접(접두어 없음): 앱 포트 +2. 개발(Vite)에서는 개발 MCP(8042).
 */
export function mcpUrl(): string {
  // 브라우저 밖(시험)에서는 빈 주소 — 번들 검사가 「localhost:80…」 이 박힌 것을 막는다.
  if (typeof window === 'undefined') return '/mcp'
  const { protocol, hostname, origin, port } = window.location
  if (PUBLIC_PATH) return `${origin}${PUBLIC_PATH}/mcp`
  if (import.meta.env.DEV) return `${protocol}//${hostname}:8042/mcp`
  const app = Number(port || (protocol === 'https:' ? 443 : 80))
  return `${protocol}//${hostname}:${app + 2}/mcp`
}

export function setupSnippets(token: string | null, url = mcpUrl()) {
  const tok = token || TOKEN_PLACEHOLDER
  const name = APP_SLUG
  const bridgeArgs = ['-y', 'mcp-remote', url, '--allow-http', '--header', 'Authorization:${AUTH}']
  const env = { AUTH: `Bearer ${tok}`, NODE_OPTIONS: '--use-system-ca' }

  const claudeCode =
    `claude mcp add ${name} \\\n` +
    `  -e AUTH='Bearer ${tok}' \\\n` +
    `  -e NODE_OPTIONS=--use-system-ca \\\n` +
    `  -- npx -y mcp-remote ${url} --allow-http --header 'Authorization:\${AUTH}'`

  const desktop = `"${name}": ${JSON.stringify({ command: 'npx', args: bridgeArgs, env }, null, 2)}`

  const codex =
    `[mcp_servers.${name}]\n` +
    `command = "npx"\n` +
    `args = ${JSON.stringify(bridgeArgs)}\n\n` +
    `[mcp_servers.${name}.env]\n` +
    `AUTH = "Bearer ${tok}"\n` +
    `NODE_OPTIONS = "--use-system-ca"`

  return { claudeCode, desktop, codex, gemini: desktop }
}

function Snippet({ text, label }: { text: string; label: string }) {
  const [done, setDone] = useState<'ok' | 'fail' | null>(null)
  return (
    <div className="space-y-2">
      <pre className="bg-muted overflow-x-auto rounded px-2 py-2 font-mono text-[11px] whitespace-pre">
        {text}
      </pre>
      <div className="flex items-center gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() =>
            copyText(text)
              .then(() => setDone('ok'))
              .catch(() => setDone('fail'))
          }
        >
          {label} 복사
        </Button>
        {done === 'ok' && <span className="text-muted-foreground text-xs">복사됨</span>}
        {done === 'fail' && (
          <span className="text-destructive text-xs">복사 실패 — 직접 선택해 복사하세요</span>
        )}
      </div>
    </div>
  )
}

export function McpSetupGuide({ token }: { token: string | null }) {
  const url = mcpUrl()
  const s = setupSnippets(token, url)
  return (
    <section className="space-y-3 rounded-md border p-4">
      <div>
        <h3 className="text-sm font-semibold">AI 도구 연결</h3>
        <p className="text-muted-foreground mt-1 text-xs">
          사용하는 도구의 탭을 선택해 복사하세요. 네 도구 모두 <b>Node.js</b> 가 있어야 합니다 —{' '}
          <span className="font-mono">npx mcp-remote</span> 다리로 붙습니다. MCP 주소:{' '}
          <span className="font-mono">{url}</span>
        </p>
      </div>
      {!token && (
        <div className="bg-muted/40 text-muted-foreground rounded-md border border-dashed px-3 py-2 text-xs">
          아직 토큰을 발급하지 않았습니다. 아래는 예시 형식이고 토큰 자리에{' '}
          <span className="font-mono">{TOKEN_PLACEHOLDER}</span> 이 들어 있습니다. 위에서 발급하면
          실제 토큰이 채워집니다.
        </div>
      )}
      <Tabs defaultValue="claude-code" className="w-full">
        <TabsList className="w-full flex-wrap justify-start">
          <TabsTrigger value="claude-code">Claude Code</TabsTrigger>
          <TabsTrigger value="desktop">Claude Desktop</TabsTrigger>
          <TabsTrigger value="gemini">Gemini CLI</TabsTrigger>
          <TabsTrigger value="codex">Codex CLI</TabsTrigger>
        </TabsList>
        <TabsContent value="claude-code" className="space-y-2">
          <p className="text-muted-foreground text-xs">
            터미널에 붙여넣으면 등록됩니다. 확인: <span className="font-mono">claude mcp list</span>
          </p>
          <Snippet text={s.claudeCode} label="명령" />
        </TabsContent>
        <TabsContent value="desktop" className="space-y-2">
          <p className="text-muted-foreground text-xs">
            설정 → 개발자 → 「설정 편집」으로{' '}
            <span className="font-mono">claude_desktop_config.json</span> 을 열고, 아래 항목을{' '}
            <span className="font-mono">{'"mcpServers": { }'}</span> 중괄호 안에 붙여넣은 뒤 Claude
            Desktop 을 완전히 종료했다가 다시 켭니다. 다른 항목이 이미 있으면 사이에 쉼표.
          </p>
          <Snippet text={s.desktop} label="항목" />
        </TabsContent>
        <TabsContent value="gemini" className="space-y-2">
          <p className="text-muted-foreground text-xs">
            <span className="font-mono">~/.gemini/settings.json</span> 의{' '}
            <span className="font-mono">{'"mcpServers": { }'}</span> 안에 붙여넣습니다(Claude
            Desktop 과 같은 모양). 확인: <span className="font-mono">gemini</span> 실행 후{' '}
            <span className="font-mono">/mcp</span>
          </p>
          <Snippet text={s.gemini} label="항목" />
        </TabsContent>
        <TabsContent value="codex" className="space-y-2">
          <p className="text-muted-foreground text-xs">
            <span className="font-mono">~/.codex/config.toml</span> 끝에 붙여넣습니다.
          </p>
          <Snippet text={s.codex} label="설정" />
        </TabsContent>
      </Tabs>
    </section>
  )
}
