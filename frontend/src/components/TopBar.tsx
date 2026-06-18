import { Cpu, X } from 'lucide-react'
import type { SourceEvent, Status } from '../types'

function Badge({ children, tone = 'neutral', dot = false }: {
  children: React.ReactNode
  tone?: 'neutral' | 'live' | 'ok'
  dot?: boolean
}) {
  const tones = {
    neutral: 'bg-ink-700 text-fg-dim',
    live: 'bg-live/15 text-live',
    ok: 'bg-ok/10 text-ok',
  }
  const dotColor = { neutral: 'bg-fg-faint', live: 'bg-live', ok: 'bg-ok' }
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-semibold ${tones[tone]}`}>
      {dot && <span className={`size-1.5 rounded-full ${dotColor[tone]} ${tone === 'live' ? 'animate-pulse' : ''}`} />}
      {children}
    </span>
  )
}

export function TopBar({ source, status, onExit }: {
  source: SourceEvent
  status: Status
  onExit: () => void
}) {
  const ended = status.state === 'ended'
  const kind = source.is_live && !ended
    ? <Badge tone="live" dot>LIVE</Badge>
    : source.media
      ? <Badge tone="ok" dot>Offline</Badge>
      : <Badge>{ended ? 'Ended' : source.live_status === 'was_live' || source.live_status === 'post_live' ? 'Replay' : 'Video'}</Badge>

  return (
    <header className="flex h-[58px] shrink-0 items-center gap-4 border-b border-line bg-ink-900 px-4.5">
      <button onClick={onExit} title="Bifröst home"
        className="flex cursor-pointer items-center gap-2.5 bg-transparent">
        <svg width="24" height="24" viewBox="0 0 32 32" fill="none" aria-hidden>
          <defs>
            <linearGradient id="bf-arc-tb" x1="3" y1="25" x2="29" y2="25" gradientUnits="userSpaceOnUse">
              <stop offset="0" stopColor="#34d399" /><stop offset=".5" stopColor="#38bdf8" /><stop offset="1" stopColor="#a78bfa" />
            </linearGradient>
          </defs>
          <path d="M3 24.5 A 13 13 0 0 1 29 24.5" stroke="url(#bf-arc-tb)" strokeWidth="4" strokeLinecap="round" />
          <circle cx="3" cy="24.5" r="2.6" fill="#34d399" /><circle cx="29" cy="24.5" r="2.6" fill="#a78bfa" />
        </svg>
        <span className="text-[17px] font-extrabold tracking-tight text-white">Bifröst</span>
      </button>

      <div className="h-6 w-px bg-line" />

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2.5">
          {kind}
          <span className="max-w-[460px] truncate text-sm font-medium text-fg">{source.title}</span>
        </div>
        <div className="mt-0.5 text-xs text-fg-dim">{source.channel}</div>
      </div>

      <div className="flex items-center gap-2.5">
        <span title="Bifröst runs on your machine. Audio never leaves your device."
          className="inline-flex items-center gap-1.5 rounded-full bg-ok/10 px-2.5 py-1 text-[11px] font-medium text-ok">
          <Cpu size={13} /> On-device · {source.model}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-fg-faint">Output</span>
          <span className="inline-flex items-center gap-1.5 rounded-full border border-accent-500/40 bg-accent-500/10
                           px-2.5 py-1 text-xs font-medium text-fg">
            🇬🇧 English <span className="font-mono text-[10px] text-accent-300">EN</span>
          </span>
        </span>
        <button onClick={onExit} aria-label="Stop and go home"
          className="grid size-8 cursor-pointer place-items-center rounded-lg text-fg-dim transition-colors hover:bg-ink-700 hover:text-fg">
          <X size={16} />
        </button>
      </div>
    </header>
  )
}
