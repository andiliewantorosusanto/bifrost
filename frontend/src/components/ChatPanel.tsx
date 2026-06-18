import { useEffect, useRef, useState } from 'react'
import { Globe, MessageSquare, PanelRightClose } from 'lucide-react'
import type { ChatItem, ChatStatus } from '../types'

function fmtClock(iso?: string): string | null {
  if (!iso) return null
  const d = new Date(iso)
  if (isNaN(d.getTime())) return null
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

const AVATAR_HUES = [200, 260, 320, 20, 80, 140]
function Avatar({ name }: { name: string }) {
  const hue = AVATAR_HUES[(name.charCodeAt(0) || 0) % AVATAR_HUES.length]
  return (
    <span className="grid size-7 shrink-0 place-items-center rounded-full text-[11px] font-bold text-white"
      style={{ background: `hsl(${hue} 45% 38%)` }}>
      {name.slice(0, 1).toUpperCase()}
    </span>
  )
}

function Message({ m }: { m: ChatItem }) {
  const [showOrig, setShowOrig] = useState(false)
  const time = fmtClock(m.published_at)
  return (
    <div className="flex gap-2.5 rounded-lg px-2.5 py-2 hover:bg-ink-800/60">
      <Avatar name={m.author} />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className={`truncate text-xs font-semibold ${m.mod ? 'text-accent-300' : 'text-fg-dim'}`}>
            {m.author}
          </span>
          {m.src && m.src !== 'EN' && (
            <span className="rounded bg-ink-700 px-1 font-mono text-[9px] text-fg-faint">{m.src}</span>
          )}
          {time && <span className="ml-auto shrink-0 font-mono text-[10px] text-fg-faint">{time}</span>}
        </div>
        <div className="mt-0.5 text-sm leading-snug text-fg [overflow-wrap:anywhere]">{m.text}</div>
        {m.original && (
          <button onClick={() => setShowOrig(o => !o)}
            className="mt-0.5 cursor-pointer text-[11px] text-accent-300/80 hover:text-accent-300">
            {showOrig ? m.original : 'Show original'}
          </button>
        )}
      </div>
    </div>
  )
}

export function ChatPanel({ items, chatStatus, isLive, open, onToggle }: {
  items: ChatItem[]
  chatStatus: ChatStatus | null
  isLive: boolean
  open: boolean
  onToggle: () => void
}) {
  const listRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (open && listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight
  }, [items.length, open])

  const ok = chatStatus?.ok ?? false

  // Collapsed: a slim rail. While closed, the backend stops polling chat (saves
  // API quota), so reopening resumes the feed from "now".
  if (!open) {
    return (
      <aside className="flex h-full min-h-0 flex-col items-center gap-3 border-l border-line bg-ink-900 py-3">
        <button onClick={onToggle} title="Show live chat"
          className="grid size-7 place-items-center rounded-md text-fg-faint hover:bg-ink-800 hover:text-fg">
          <MessageSquare size={16} />
        </button>
        <span className={`size-1.5 rounded-full ${ok && isLive ? 'bg-live' : 'bg-fg-faint/40'}`} />
      </aside>
    )
  }

  return (
    <aside className="flex h-full min-h-0 flex-col border-l border-line bg-ink-900">
      <div className="shrink-0 border-b border-line px-4 py-3.5">
        <div className="flex items-center justify-between">
          <span className="text-[15px] font-semibold text-fg">Live chat</span>
          <div className="flex items-center gap-2">
            {ok && (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-accent-500/10 px-2.5 py-1
                               text-[11px] font-semibold text-accent-300">
                <span className="size-1.5 rounded-full bg-accent-400" /> Translated
              </span>
            )}
            <button onClick={onToggle} title="Hide live chat (pauses polling)"
              className="grid size-6 place-items-center rounded-md text-fg-faint hover:bg-ink-800 hover:text-fg">
              <PanelRightClose size={15} />
            </button>
          </div>
        </div>
        <div className="mt-2 flex items-center gap-1.5 text-fg-faint">
          <Globe size={13} />
          <span className="text-xs">From YouTube · every message in English</span>
        </div>
      </div>

      <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto p-2">
        {!ok && (
          <div className="px-3 py-4 text-sm leading-relaxed text-fg-faint">
            {chatStatus ? chatStatus.message : 'Connecting to live chat…'}
          </div>
        )}
        {items.map((m, i) => <Message key={i} m={m} />)}
      </div>

      <div className="flex shrink-0 items-center justify-center gap-2 border-t border-line px-3.5 py-2.5">
        <span className={`size-1.5 rounded-full ${ok && isLive ? 'bg-live' : 'bg-fg-faint/50'}`} />
        <span className="text-[11px] text-fg-faint">
          {ok ? 'Live from YouTube · translated to English' : 'Chat unavailable'}
        </span>
      </div>
    </aside>
  )
}
