import { useState } from 'react'
import { Captions, Cpu, Download, Link2, Loader2, Trash2 } from 'lucide-react'
import type { LibraryItem, Status } from '../types'
import { fmtMedia } from '../players'

function LibraryList({ items, onStart, onDelete }: {
  items: LibraryItem[]
  onStart: (url: string) => void
  onDelete: (videoId: string) => void
}) {
  if (!items.length) return null
  return (
    <div className="mt-9 text-left">
      <h3 className="mb-2.5 text-[11px] font-semibold uppercase tracking-[0.12em] text-fg-faint">
        Library
      </h3>
      <div className="flex max-h-64 flex-col gap-2 overflow-y-auto pr-1">
        {items.map(it => (
          <button
            key={it.video_id}
            onClick={() => onStart(`https://www.youtube.com/watch?v=${it.video_id}`)}
            className="flex w-full cursor-pointer items-center gap-3 rounded-lg border border-line
                       bg-ink-900 px-3.5 py-2.5 text-left transition-colors hover:border-line-strong hover:bg-ink-800"
          >
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm text-fg">{it.title}</div>
              <div className="mt-0.5 text-xs text-fg-faint">
                {it.channel}{it.duration ? ` · ${fmtMedia(it.duration)}` : ''}
              </div>
            </div>
            <span className="flex shrink-0 items-center gap-2">
              {it.has_media && (
                <span title="Video saved — plays offline" className="text-ok"><Download size={13} /></span>
              )}
              {it.has_captions && (
                <span title="Transcript cached — no reprocessing" className="text-accent-300"><Captions size={13} /></span>
              )}
              <span
                role="button"
                title="Remove from library (video + cached transcript)"
                onClick={e => { e.stopPropagation(); onDelete(it.video_id) }}
                className="cursor-pointer rounded p-1 text-fg-faint transition-colors hover:bg-live/15 hover:text-live"
              >
                <Trash2 size={13} />
              </span>
            </span>
          </button>
        ))}
      </div>
    </div>
  )
}

export function ConnectScreen({ onStart, status, connected, libraryItems, onDelete }: {
  onStart: (url: string) => void
  status: Status
  connected: boolean
  libraryItems: LibraryItem[]
  onDelete: (videoId: string) => void
}) {
  const [url, setUrl] = useState('')
  const busy = status.state === 'probing'
  const error = status.state === 'error' ? status.message : null

  return (
    <div className="grid min-h-full place-items-center overflow-y-auto p-6"
      style={{ background: 'radial-gradient(110% 80% at 50% -10%, rgba(14,165,233,0.10), transparent 60%)' }}>
      <div className="w-[min(480px,92vw)] text-center">
        <span className="mb-5 inline-flex items-center gap-1.5 rounded-full border border-line bg-ink-900
                         px-3 py-1.5 text-[11px] font-medium text-fg-dim">
          <Cpu size={13} /> Running locally · {location.host}
        </span>

        <div className="mx-auto mb-6 grid size-14 place-items-center rounded-2xl border border-line bg-ink-900">
          <svg width="34" height="34" viewBox="0 0 32 32" fill="none" aria-label="Bifröst">
            <defs>
              <linearGradient id="bf-arc" x1="3" y1="25" x2="29" y2="25" gradientUnits="userSpaceOnUse">
                <stop offset="0" stopColor="#34d399" /><stop offset=".5" stopColor="#38bdf8" /><stop offset="1" stopColor="#a78bfa" />
              </linearGradient>
            </defs>
            <path d="M3 24.5 A 13 13 0 0 1 29 24.5" stroke="url(#bf-arc)" strokeWidth="4" strokeLinecap="round" />
            <circle cx="3" cy="24.5" r="2.6" fill="#34d399" /><circle cx="29" cy="24.5" r="2.6" fill="#a78bfa" />
          </svg>
        </div>

        <h1 className="mb-2.5 text-[32px] font-extrabold leading-tight tracking-tight text-white">
          Any stream,<br />understood in English
        </h1>
        <p className="mx-auto mb-7 max-w-[400px] text-[15px] leading-relaxed text-fg-dim">
          Paste any YouTube link — a live stream or a regular video. Bifröst transcribes the
          audio and translates the captions and chat into English, right on your machine.
        </p>

        <div className="flex flex-col gap-3 text-left">
          <label className="flex flex-col gap-1.5">
            <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-fg-faint">YouTube link</span>
            <span className="flex items-center gap-2 rounded-lg border border-line bg-ink-900 px-3
                             focus-within:border-accent-500">
              <Link2 size={15} className="shrink-0 text-fg-faint" />
              <input
                value={url}
                onChange={e => setUrl(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && url && !busy) onStart(url) }}
                placeholder="https://youtube.com/watch?v=…"
                className="w-full bg-transparent py-2.5 text-sm text-fg outline-none placeholder:text-fg-faint"
              />
            </span>
          </label>
          <button
            onClick={() => onStart(url)}
            disabled={busy || !connected || !url}
            className="mt-1 flex h-12 w-full cursor-pointer items-center justify-center gap-2 rounded-lg
                       bg-gradient-to-r from-emerald-500 via-sky-500 to-violet-500 text-[15px] font-semibold
                       text-white transition-opacity hover:opacity-95 disabled:cursor-default disabled:opacity-40"
          >
            {busy ? <><Loader2 size={16} className="animate-spin" /> Starting local engine…</> : 'Translate to English'}
          </button>
        </div>

        {error && <p className="mt-4 text-sm text-live">{error}</p>}
        {!connected && <p className="mt-4 text-sm text-warn">Connecting to the local engine…</p>}

        <p className="mt-5 text-[11px] leading-relaxed text-fg-faint">
          On-device · audio never leaves your computer<br />
          Source language detected automatically · English output
        </p>

        <LibraryList items={libraryItems} onStart={onStart} onDelete={onDelete} />
      </div>
    </div>
  )
}
