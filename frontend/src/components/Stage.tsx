import { useEffect, useRef, useState } from 'react'
import {
  Captions, Download, Maximize, Pause, Play, RefreshCw, SlidersHorizontal,
  Volume2, VolumeX, X,
} from 'lucide-react'
import type { CapSize, Caption, DownloadStatus, PipelineStatus, SourceEvent, Status } from '../types'
import { capTime, fmtClockEpoch, fmtMedia, useNativePlayer, useYouTubePlayer } from '../players'
import { useNow, usePref } from '../useBifrost'
import { CaptionView } from './Caption'

interface CapPos { x: number; y: number } // % of stage width/height (box center)

function CtrlButton({ label, active = false, onClick, children }: {
  label: string
  active?: boolean
  onClick?: () => void
  children: React.ReactNode
}) {
  return (
    <button onClick={onClick} aria-label={label} title={label}
      className={`grid size-8 shrink-0 cursor-pointer place-items-center rounded-md transition-colors
                  ${active ? 'bg-accent-500/25 text-accent-300' : 'text-white/85 hover:bg-white/10'}`}>
      {children}
    </button>
  )
}

function SizeSwitch({ value, onChange }: { value: CapSize; onChange: (v: CapSize) => void }) {
  const opts: [CapSize, string][] = [['sm', 'S'], ['md', 'M'], ['lg', 'L'], ['xl', 'XL']]
  return (
    <span title="Caption size" className="inline-flex items-center gap-0.5 rounded-full bg-ink-700 p-0.5">
      {opts.map(([v, l]) => (
        <button key={v} onClick={() => onChange(v)} aria-label={`Caption size ${l}`}
          className={`cursor-pointer rounded-full px-2.5 py-1 font-mono text-[11px] transition-colors
                      ${value === v ? 'bg-accent-600 text-white' : 'text-fg-faint hover:text-fg-dim'}`}>
          {l}
        </button>
      ))}
    </span>
  )
}

export function Stage({ source, captions, status, dual, setDual, overlayOn, setOverlayOn,
                        capSize, setCapSize, download, onDownload, onRegenerate, pipeline }: {
  source: SourceEvent
  captions: Caption[]
  status: Status
  dual: boolean
  setDual: (f: (d: boolean) => boolean) => void
  overlayOn: boolean
  setOverlayOn: (f: (o: boolean) => boolean) => void
  capSize: CapSize
  setCapSize: (s: CapSize) => void
  download: DownloadStatus | null
  onDownload: () => void
  onRegenerate: () => void
  pipeline: PipelineStatus | null
}) {
  const media = source.media ?? null
  const yt = useYouTubePlayer(media ? null : source.video_id, source.is_live)
  const nat = useNativePlayer(media)
  const { api, error, time, duration, playing, muted, ready } = media ? nat : yt
  const frameRef = useRef<HTMLDivElement>(null)
  const [fs, setFs] = useState(false)
  const now = useNow(1000)

  // Draggable caption position (% of stage, persisted; null = bottom-center).
  const [capPosPref, setCapPosPref] = usePref<CapPos | null>('capPos', null)
  const [capPos, setCapPos] = useState<CapPos | null>(capPosPref)

  function startCapDrag(e: React.PointerEvent) {
    const stage = frameRef.current
    if (!stage) return
    e.preventDefault()
    const rect = stage.getBoundingClientRect()
    let latest: CapPos | null = null
    const move = (ev: PointerEvent) => {
      latest = {
        x: Math.min(95, Math.max(5, ((ev.clientX - rect.left) / rect.width) * 100)),
        y: Math.min(92, Math.max(5, ((ev.clientY - rect.top) / rect.height) * 100)),
      }
      setCapPos(latest)
    }
    const up = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      if (latest) setCapPosPref(latest) // persist once, after the drag
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  function resetCapPos() {
    setCapPos(null)
    setCapPosPref(null)
  }

  useEffect(() => {
    const onChange = () => setFs(!!document.fullscreenElement)
    document.addEventListener('fullscreenchange', onChange)
    return () => document.removeEventListener('fullscreenchange', onChange)
  }, [])

  function toggleFs() {
    const el = frameRef.current
    if (!el) return
    if (!fs) el.requestFullscreen()
    else document.exitFullscreen()
  }

  function onSeek(e: React.MouseEvent<HTMLDivElement>) {
    const rect = e.currentTarget.getBoundingClientRect()
    api.seek((e.clientX - rect.left) / rect.width)
  }

  // Live (or no working player): newest caption. Video: caption at the playhead.
  let current: Caption | null = null
  if (source.is_live || error) {
    current = captions[captions.length - 1] ?? null
  } else {
    for (const c of captions) {
      if (c.t0 <= time + 0.5) current = c
      else break
    }
  }

  const progressPct = source.is_live ? 100 : duration ? Math.min(100, (time / duration) * 100) : 0

  // Live lag, measured to the MIDDLE of the caption's audio — i.e. how long
  // ago (in displayed-video time) the words you're reading were spoken.
  // endGap aligns the caption's audio end with the playhead via the player's
  // own distance to the live edge; a caption then spans the chunk before that.
  let lagShort = `~${source.chunk_seconds}s behind`
  if (source.is_live && current?.captured_at) {
    const captionAge = now / 1000 - current.captured_at
    const playerBehind = duration > 0 && time > 0 && duration > time ? duration - time : 0
    if (playerBehind > 60) {
      // Embed still parked in the DVR window, seeking to the live edge — any lag
      // number here is the DVR length, not real caption lag. (See players.ts.)
      lagShort = 'syncing to live…'
    } else {
      const midGap = Math.round(captionAge - playerBehind + source.chunk_seconds / 2)
      lagShort = midGap >= -4
        ? `~${Math.max(midGap, Math.round(source.chunk_seconds / 2))}s behind`
        : `~${-midGap}s ahead`
    }
  }
  const liveLagNote = `captions ${lagShort}`

  // One compact status chip; the full story lives in its tooltip.
  const dl = pipeline?.speed ?? null
  const waiting = pipeline != null && pipeline.speed === 0 && pipeline.captured_s === 0
  const chipTitle = [
    source.is_live
      ? 'How far the words you’re reading are behind what the video is showing.'
      : pipeline?.local
        ? 'Transcribing the saved file — paced by Whisper on your GPU, no network involved.'
        : error
          ? 'No playable embed — captions appear as they’re processed.'
          : 'The transcript builds ahead of playback and follows your playhead.',
    dl != null && !pipeline?.local ? `download ${dl.toFixed(1)}× real-time` : null,
    pipeline?.target_s && !source.is_live
      ? `captured ${fmtMedia(pipeline.captured_s)} of ${fmtMedia(pipeline.target_s)}`
      : null,
    pipeline?.whisper_ms != null ? `whisper ${(pipeline.whisper_ms / 1000).toFixed(1)}s per chunk` : null,
    'all processing on-device',
  ].filter(Boolean).join(' · ')
  let chipText: string
  let chipTone = 'text-fg-faint'
  let chipDot = 'bg-emerald-400'
  if (status.state === 'running' && pipeline?.local) {
    // Saved file: Whisper is the pace-setter, "throttled" would be a lie.
    chipText = `processing ${fmtMedia(pipeline.captured_s)}`
      + (pipeline.target_s ? `/${fmtMedia(pipeline.target_s)}` : '')
      + (pipeline.whisper_ms != null ? ` · whisper ${(pipeline.whisper_ms / 1000).toFixed(1)}s` : '')
  } else if (status.state === 'running' && waiting) {
    chipText = 'waiting for YouTube…'; chipTone = 'text-warn'; chipDot = 'bg-warn'
  } else if (source.is_live) {
    chipText = `${lagShort} · now ${fmtClockEpoch(now / 1000)}`
    if (dl != null && dl < 0.9) { chipText += ` · dl ${dl.toFixed(1)}×`; chipTone = 'text-live'; chipDot = 'bg-live' }
  } else if (error) {
    chipText = 'captions as processed'
  } else if (status.state === 'running' && dl != null) {
    chipText = `dl ${dl.toFixed(1)}×${dl < 0.9 ? ' throttled' : ''}`
    if (pipeline?.target_s) chipText += ` · ${fmtMedia(pipeline.captured_s)}/${fmtMedia(pipeline.target_s)}`
    if (dl < 0.9) { chipTone = 'text-live'; chipDot = 'bg-live' }
  } else {
    chipText = 'synced to playback'
  }

  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!menuOpen) return
    const onDoc = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [menuOpen])

  const downloading = download?.state === 'downloading'

  return (
    <div>
      {/* key forces a clean remount when the player type flips (embed → native
          after an offline download): the YT IFrame API replaces its mount <div>
          with an iframe behind React's back, so reconciling across the swap
          throws removeChild and blanks the whole app. */}
      <div ref={frameRef} key={media ? 'native' : 'embed'}
        className="stage-fs relative aspect-video w-full overflow-hidden rounded-xl border border-line bg-black">
        {media
          ? <video ref={nat.ref} src={media} autoPlay playsInline
              className="absolute inset-0 h-full w-full bg-black" />
          : <div ref={yt.ref} className="absolute inset-0 h-full w-full [&>iframe]:h-full [&>iframe]:w-full" />}

        {error && (
          <div className="absolute inset-0 grid place-items-center p-6 text-center"
            style={{ background: 'radial-gradient(60% 50% at 78% 85%, rgba(167,139,250,0.25), transparent 70%), radial-gradient(50% 45% at 18% 88%, rgba(52,211,153,0.18), transparent 70%), radial-gradient(120% 120% at 30% 18%, #14202e 0%, #10141f 45%, #0a0c10 100%)' }}>
            <div className="max-w-[420px]">
              <p className="mb-2 text-base font-semibold text-white">
                {error === 100 ? 'This video is private or removed.' : 'This video can’t be played in an embedded player.'}
              </p>
              <p className="mb-4 text-sm text-fg-dim">
                Translation keeps running — captions appear here as they’re processed.
                Save it offline to play it right here, or watch on YouTube alongside.
              </p>
              <div className="flex items-center justify-center gap-4">
                <button onClick={onDownload} disabled={downloading}
                  className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-line-strong
                             bg-ink-800 px-3.5 py-2 text-sm text-fg transition-colors hover:bg-ink-700 disabled:opacity-60">
                  <Download size={14} />
                  {downloading ? `Saving… ${download?.progress ?? 0}%` : 'Save offline & play here'}
                </button>
                <a href={`https://www.youtube.com/watch?v=${source.video_id}`} target="_blank" rel="noreferrer"
                  className="text-sm text-accent-300 hover:underline">
                  Open on YouTube ↗
                </a>
              </div>
            </div>
          </div>
        )}

        {/* click shield: the stage behaves like one player surface */}
        {!error && <div className="absolute inset-0 cursor-pointer" onClick={api.toggle} />}
        {!error && ready && !playing && (
          <button onClick={api.toggle} aria-label="Play"
            className="absolute left-1/2 top-1/2 grid size-[76px] -translate-x-1/2 -translate-y-1/2 cursor-pointer
                       place-items-center rounded-full border border-white/20 bg-ink-950/50 text-white backdrop-blur-lg">
            <Play size={30} className="ml-1" />
          </button>
        )}

        {/* fullscreen keeps the lag + clock info that normally sits under the player */}
        {fs && source.is_live && (
          <div className="pointer-events-none absolute right-3 top-3 flex items-center gap-2 rounded-md
                          bg-ink-950/60 px-2.5 py-1.5 font-mono text-xs text-white/75 backdrop-blur-md">
            <span className="size-1 rounded-full bg-emerald-400" />
            {liveLagNote} · now {fmtClockEpoch(now / 1000)}
          </div>
        )}

        {/* caption overlay — drag to move, double-click to reset position */}
        {overlayOn && current && (
          <div
            className={`pointer-events-none absolute ${capPos ? '' : 'inset-x-4 bottom-[70px] flex justify-center'}`}
            style={capPos ? {
              left: `${capPos.x}%`, top: `${capPos.y}%`,
              transform: 'translate(-50%, -50%)', maxWidth: '92%',
            } : undefined}
          >
            <div
              onPointerDown={startCapDrag}
              onDoubleClick={resetCapPos}
              title="Drag to move · double-click to reset"
              className="pointer-events-auto cursor-grab select-none touch-none active:cursor-grabbing"
            >
              <CaptionView overlay size={capSize} live={source.is_live}
                time={capTime(current, source.is_live)} text={current.text}
                original={dual ? current.original : undefined} />
            </div>
          </div>
        )}
        {overlayOn && !current && status.state === 'running' && (
          <div className="pointer-events-none absolute inset-x-4 bottom-[70px] flex justify-center">
            <span className="rounded-md bg-ink-950/60 px-3 py-1.5 text-sm text-white/65 backdrop-blur-md">
              Listening… first caption arrives after the first chunk.
            </span>
          </div>
        )}

        {/* Bifröst's own control bar (the player is chromeless) */}
        <div className="absolute inset-x-0 bottom-0 flex items-center gap-2 bg-gradient-to-t
                        from-ink-950/95 to-transparent px-3.5 py-2.5">
          {!error && <>
            <CtrlButton label={playing ? 'Pause' : 'Play'} onClick={api.toggle}>
              {playing ? <Pause size={17} /> : <Play size={17} />}
            </CtrlButton>
            <CtrlButton label={muted ? 'Unmute' : 'Mute'} active={muted} onClick={api.toggleMute}>
              {muted ? <VolumeX size={17} /> : <Volume2 size={17} />}
            </CtrlButton>
            {source.is_live
              ? <span className="flex items-center gap-1.5 font-mono text-xs text-white/85">
                  <span className="size-1.5 animate-pulse rounded-full bg-live" />LIVE
                </span>
              : <span className="whitespace-nowrap font-mono text-xs text-white/70">
                  {fmtMedia(time)} / {fmtMedia(duration)}
                </span>}
            <div onClick={source.is_live ? undefined : onSeek}
              className={`relative h-1 flex-1 rounded-full bg-white/15 ${source.is_live ? '' : 'cursor-pointer'}`}>
              <div className={`absolute inset-y-0 left-0 rounded-full ${source.is_live ? 'bg-live' : 'bg-accent-500'}`}
                style={{ width: `${progressPct}%` }} />
            </div>
          </>}
          {error && <span className="flex-1" />}
          <CtrlButton label="Captions" active={overlayOn} onClick={() => setOverlayOn(o => !o)}>
            <Captions size={17} />
          </CtrlButton>
          <CtrlButton label={fs ? 'Exit fullscreen' : 'Fullscreen'} onClick={toggleFs}>
            {fs ? <X size={17} /> : <Maximize size={17} />}
          </CtrlButton>
        </div>
      </div>

      {/* under-player row: language pills · one status chip · save · captions menu */}
      <div className="mt-3 flex items-center gap-2.5">
        <span className="shrink-0 rounded-full border border-line bg-ink-900 px-2.5 py-1 text-xs text-fg-dim">
          Source · auto
        </span>
        <span className="shrink-0 text-fg-faint">→</span>
        <span className="shrink-0 rounded-full border border-accent-500/40 bg-accent-500/10 px-2.5 py-1 text-xs text-fg">
          🇬🇧 English <span className="font-mono text-[10px] text-accent-300">EN</span>
        </span>

        <span className={`inline-flex min-w-0 items-center gap-1.5 truncate font-mono text-xs ${chipTone}`}
          title={chipTitle}>
          <span className={`size-1.5 shrink-0 rounded-full ${chipDot}`} />
          <span className="truncate">{chipText}</span>
        </span>

        <span className="ml-auto flex shrink-0 items-center gap-3">
          {!source.is_live && (
            media || download?.state === 'done'
              ? <span className="inline-flex items-center gap-1.5 text-xs font-medium text-ok">
                  <Download size={13} /> Saved
                </span>
              : <button onClick={onDownload} disabled={downloading}
                  title="Download the video and cache the transcript for offline playback"
                  className={`inline-flex cursor-pointer items-center gap-1.5 text-xs
                              ${download?.state === 'error' ? 'text-live' : 'text-fg-dim hover:text-fg'}`}>
                  <Download size={13} />
                  {downloading ? `Saving… ${download?.progress ?? 0}%`
                    : download?.state === 'error' ? 'Retry save' : 'Save offline'}
                </button>
          )}
          <div className="relative" ref={menuRef}>
            <button onClick={() => setMenuOpen(o => !o)}
              className={`inline-flex cursor-pointer items-center gap-1.5 text-xs
                          ${menuOpen ? 'text-fg' : 'text-fg-dim hover:text-fg'}`}>
              <SlidersHorizontal size={13} /> Captions
            </button>
            {menuOpen && (
              <div className="absolute right-0 top-full z-20 mt-2 w-60 rounded-lg border border-line
                              bg-ink-800 p-3 shadow-xl shadow-black/40">
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-fg-faint">Size</span>
                  <SizeSwitch value={capSize} onChange={setCapSize} />
                </div>
                <button onClick={() => setDual(d => !d)}
                  className={`block w-full cursor-pointer py-1 text-left text-xs
                              ${dual ? 'text-accent-300' : 'text-fg-dim hover:text-fg'}`}>
                  {dual ? '✓ ' : ''}Show original + translation
                </button>
                {source.cached && (
                  <button onClick={() => { setMenuOpen(false); onRegenerate() }}
                    title="Discard the cached transcript and run Whisper again (uses the saved video file if present)"
                    className="mt-1 flex w-full cursor-pointer items-center gap-1.5 border-t border-line
                               pt-2 text-left text-xs text-fg-dim hover:text-fg">
                    <RefreshCw size={12} /> Regenerate transcript
                  </button>
                )}
              </div>
            )}
          </div>
        </span>
      </div>
    </div>
  )
}
