/**
 * 목록 화면 왼쪽의 트리 — **거대한 거르개.**
 *
 * 사이드바가 아닌 이유: 사이드바는 「어디로 갈까」 를 말하는 자리고 트리는
 * 「무엇이 무엇 아래 있나」 를 말하는 자리다. 다른 물음이다. 그리고 객체는 수천
 * 개가 될 수 있는데, 사이드바에 넣으면 **매 화면에서 그것을 이고 다닌다.**
 *
 * 노드를 고르면 오른쪽 목록이 그 아래로 좁혀진다. 파일 탐색기와 같은 손놀림이라
 * 배울 것이 없다.
 */

import { useEffect, useState } from 'react'
import { ChevronRight, FolderTree } from 'lucide-react'

import { objectApi } from '@/modules/objects/api'
import type { TreeNode } from '@/modules/objects/api'
import { Button } from '@/shared/components/ui/button'
import { cn } from '@/shared/lib/utils'

interface Props {
  typeSlug: string
  selected: string | null
  onSelect: (id: string | null) => void
  /** 관계가 바뀌면 다시 읽는다. */
  reloadKey: number
}

export function ObjectTree({ typeSlug, selected, onSelect, reloadKey }: Props) {
  const [roots, setRoots] = useState<TreeNode[]>([])
  const [orphanCount, setOrphanCount] = useState(0)
  const [showOrphans, setShowOrphans] = useState(false)
  const [orphans, setOrphans] = useState<TreeNode[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    objectApi
      .tree(typeSlug)
      .then((data) => {
        if (cancelled) return
        setRoots(data.nodes)
        setOrphanCount(data.orphan_count)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [typeSlug, reloadKey])

  useEffect(() => {
    if (!showOrphans) return
    let cancelled = false
    objectApi.tree(typeSlug, { orphans: true }).then((data) => {
      if (!cancelled) setOrphans(data.nodes)
    })
    return () => {
      cancelled = true
    }
  }, [typeSlug, showOrphans, reloadKey])

  if (loading) return null

  return (
    <aside className="w-60 shrink-0 border-r pr-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-muted-foreground flex items-center gap-1.5 text-xs">
          <FolderTree className="size-3.5" />
          구조
        </span>
        {selected && (
          <Button variant="ghost" size="sm" className="h-6 px-2" onClick={() => onSelect(null)}>
            전체 보기
          </Button>
        )}
      </div>

      {roots.length === 0 && orphanCount === 0 ? (
        <p className="text-muted-foreground text-xs">
          아직 이어진 것이 없습니다. 객체 상세의 「관련 객체」 에서 이으면 여기 구조가 생깁니다.
        </p>
      ) : (
        <ul className="space-y-0.5">
          {roots.map((node) => (
            <Node
              key={node.id}
              typeSlug={typeSlug}
              node={node}
              depth={0}
              selected={selected}
              onSelect={onSelect}
              reloadKey={reloadKey}
            />
          ))}
        </ul>
      )}

      {/* **어디에도 안 걸린 것을 반드시 보이게 한다.** 부모가 없는 객체가 트리에
          안 나오면 눈에서 사라진 채 남고, 아무도 그것이 있다는 것을 모른다. */}
      {orphanCount > 0 && (
        <div className="mt-3 border-t pt-2">
          <button
            type="button"
            className="text-muted-foreground hover:text-foreground flex w-full items-center gap-1 text-xs"
            onClick={() => setShowOrphans((now) => !now)}
          >
            <ChevronRight
              className={cn('size-3.5 transition-transform', showOrphans && 'rotate-90')}
            />
            어디에도 안 걸린 것 ({orphanCount})
          </button>
          {showOrphans && (
            <ul className="mt-1 space-y-0.5">
              {orphans.map((node) => (
                <li key={node.id}>
                  <NodeButton
                    node={node}
                    depth={1}
                    active={selected === node.id}
                    onSelect={() => onSelect(node.id)}
                  />
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </aside>
  )
}

function Node({
  typeSlug,
  node,
  depth,
  selected,
  onSelect,
  reloadKey,
}: {
  typeSlug: string
  node: TreeNode
  depth: number
  selected: string | null
  onSelect: (id: string) => void
  reloadKey: number
}) {
  const [open, setOpen] = useState(false)
  const [children, setChildren] = useState<TreeNode[] | null>(null)

  useEffect(() => {
    // **펼칠 때 그 단계만 읽는다.** 접힌 가지를 미리 읽으면 트리가 커질수록
    // 첫 화면이 느려지고, 그 느려짐은 화면 어디에도 안 적힌다.
    if (!open || children !== null) return
    objectApi.tree(typeSlug, { parent: node.id }).then((data) => setChildren(data.nodes))
  }, [open, children, typeSlug, node.id])

  useEffect(() => {
    setChildren(null)
  }, [reloadKey])

  return (
    <li>
      <div className="flex items-center">
        {/* **자식이 없으면 펼침 표시를 안 그린다.** 있는데 비면 눌러 보고서야
            안다 — 그 한 번이 매 노드마다 반복된다. */}
        {node.child_count > 0 ? (
          <button
            type="button"
            aria-label={`${node.label} 확장`}
            aria-expanded={open}
            className="text-muted-foreground hover:text-foreground p-0.5"
            style={{ marginLeft: depth * 12 }}
            onClick={() => setOpen((now) => !now)}
          >
            <ChevronRight className={cn('size-3.5 transition-transform', open && 'rotate-90')} />
          </button>
        ) : (
          <span className="w-[18px]" style={{ marginLeft: depth * 12 }} />
        )}
        <NodeButton
          node={node}
          depth={0}
          active={selected === node.id}
          onSelect={() => onSelect(node.id)}
        />
      </div>

      {open && children && (
        <ul className="space-y-0.5">
          {children.map((child) => (
            <Node
              key={child.id}
              typeSlug={typeSlug}
              node={child}
              depth={depth + 1}
              selected={selected}
              onSelect={onSelect}
              reloadKey={reloadKey}
            />
          ))}
        </ul>
      )}
    </li>
  )
}

function NodeButton({
  node,
  depth,
  active,
  onSelect,
}: {
  node: TreeNode
  depth: number
  active: boolean
  onSelect: () => void
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      style={{ marginLeft: depth * 12 }}
      className={cn(
        'flex-1 truncate rounded px-1.5 py-1 text-left text-sm',
        active ? 'bg-accent text-accent-foreground font-medium' : 'hover:bg-accent/50',
        node.status === 'deprecated' && 'text-muted-foreground',
      )}
      title={node.key ? `${node.label} · ${node.key}` : node.label}
    >
      {node.label}
      {node.child_count > 0 && (
        <span className="text-muted-foreground ml-1 text-xs tabular-nums">{node.child_count}</span>
      )}
    </button>
  )
}
