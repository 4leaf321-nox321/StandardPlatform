/**
 * 토큰을 넣는 법은 도구마다 모양이 다르지만 **주소 · 토큰 · 다리는 같다.**
 */
import { describe, expect, it } from 'vitest'

import { setupSnippets } from './McpSetupGuide'

describe('setupSnippets', () => {
  const url = 'https://portal.example/plm/mcp'

  it('토큰이 네 도구에 다 들어간다', () => {
    const s = setupSnippets('standardplatform_pat_abc', url)
    for (const text of [s.claudeCode, s.desktop, s.codex, s.gemini]) {
      expect(text).toContain('Bearer standardplatform_pat_abc')
      expect(text).toContain(url)
      expect(text).toContain('mcp-remote')
    }
  })

  it('토큰이 없으면 자리표시자', () => {
    const s = setupSnippets(null, url)
    expect(s.claudeCode).toContain('‹발급받은_토큰›')
    expect(s.claudeCode).not.toContain('undefined')
  })

  it('Claude Desktop 항목은 붙여 넣을 수 있는 JSON 조각이고 Gemini 도 같다', () => {
    const s = setupSnippets('t', url)
    const parsed = JSON.parse(`{${s.desktop}}`)
    const entry = Object.values(parsed)[0] as {
      command: string
      args: string[]
      env: Record<string, string>
    }
    expect(entry.command).toBe('npx')
    expect(entry.args).toContain(url)
    expect(entry.env.AUTH).toBe('Bearer t')
    expect(s.gemini).toBe(s.desktop)
  })

  it('Codex 는 TOML 두 표', () => {
    const s = setupSnippets('t', url)
    expect(s.codex).toMatch(/^\[mcp_servers\.[a-z0-9]+\]\n/)
    expect(s.codex).toContain('.env]\nAUTH = "Bearer t"')
  })
})
