import { useEffect, useMemo, useRef, useState } from 'react'
import type { Caption } from './types'

/* Minimal typings for the YouTube IFrame API. */
declare global {
  interface Window { YT?: YTNamespace; onYouTubeIframeAPIReady?: () => void }
}
interface YTNamespace {
  Player: new (el: HTMLElement, opts: object) => YTPlayer
  PlayerState: { PLAYING: number }
}
interface YTPlayer {
  getCurrentTime(): number
  getDuration(): number
  getPlayerState(): number
  isMuted(): boolean
  mute(): void
  unMute(): void
  playVideo(): void
  pauseVideo(): void
  seekTo(s: number, allowSeekAhead: boolean): void
  destroy(): void
}

export interface PlayerState {
  time: number
  duration: number
  playing: boolean
  muted: boolean
  ready: boolean
}
export interface PlayerApi {
  toggle(): void
  toggleMute(): void
  seek(frac: number): void
}

const IDLE: PlayerState = { time: 0, duration: 0, playing: false, muted: false, ready: false }

/** Chromeless YouTube embed: Bifröst draws its own controls; we fullscreen the
 *  whole stage (overlay included), so YouTube's fs button is disabled. */
export function useYouTubePlayer(videoId: string | null, isLive = false) {
  const ref = useRef<HTMLDivElement>(null)
  const playerRef = useRef<YTPlayer | null>(null)
  const [st, setSt] = useState<PlayerState>(IDLE)
  const [error, setError] = useState<number | string | null>(null)

  useEffect(() => {
    if (!videoId) return
    let player: YTPlayer | undefined
    let timer: ReturnType<typeof setInterval>
    let cancelled = false
    // YouTube autoplays a live embed at the OLDEST seekable point (start of the
    // ~1h DVR window), not the live edge — so the video lags ~an hour behind the
    // captions, which yt-dlp pulls at the edge ("Ns ahead" with N≈DVR length).
    // Jump to the edge ONCE on load; one-shot so a later manual scrub-back sticks.
    let liveSynced = false
    function init() {
      const YT = window.YT
      if (cancelled || !ref.current || !YT) return
      player = new YT.Player(ref.current, {
        videoId, width: '100%', height: '100%',
        playerVars: { autoplay: 1, origin: location.origin, controls: 0, fs: 0,
                      rel: 0, iv_load_policy: 3, disablekb: 1, playsinline: 1 },
        events: {
          onReady: () => {
            playerRef.current = player!
            timer = setInterval(() => {
              try {
                const time = player!.getCurrentTime() || 0
                const duration = player!.getDuration() || 0
                // Once duration is known, snap a stranded live embed to the edge.
                if (isLive && !liveSynced && duration > 0 && duration - time > 60) {
                  player!.seekTo(duration, true)
                  liveSynced = true
                }
                setSt({
                  time, duration,
                  playing: player!.getPlayerState() === window.YT!.PlayerState.PLAYING,
                  muted: player!.isMuted(),
                  ready: true,
                })
              } catch { /* player mid-teardown */ }
            }, 500)
          },
          // 101/150: embedding disabled by the owner. 100: private/removed.
          onError: (e: { data: number }) => setError(e.data),
        },
      })
    }
    if (window.YT?.Player) init()
    else {
      const prev = window.onYouTubeIframeAPIReady
      window.onYouTubeIframeAPIReady = () => { prev?.(); init() }
      if (!document.getElementById('yt-api')) {
        const s = document.createElement('script')
        s.id = 'yt-api'
        s.src = 'https://www.youtube.com/iframe_api'
        document.head.appendChild(s)
      }
    }
    return () => {
      cancelled = true
      clearInterval(timer)
      playerRef.current = null
      player?.destroy()
    }
  }, [videoId])

  const api = useMemo<PlayerApi>(() => ({
    toggle() {
      const p = playerRef.current
      if (!p) return
      p.getPlayerState() === window.YT!.PlayerState.PLAYING ? p.pauseVideo() : p.playVideo()
    },
    toggleMute() {
      const p = playerRef.current
      if (!p) return
      p.isMuted() ? p.unMute() : p.mute()
    },
    seek(frac) {
      const p = playerRef.current
      if (!p) return
      const d = p.getDuration()
      if (d) p.seekTo(frac * d, true)
    },
  }), [])
  return { ref, api, error, ...st }
}

/** Native <video> for saved media — same shape as useYouTubePlayer. */
export function useNativePlayer(src: string | null) {
  const ref = useRef<HTMLVideoElement>(null)
  const [st, setSt] = useState<PlayerState>(IDLE)
  const [error, setError] = useState<number | string | null>(null)

  useEffect(() => {
    if (!src) return
    const v = ref.current
    if (!v) return
    const timer = setInterval(() => setSt({
      time: v.currentTime || 0,
      duration: Number.isFinite(v.duration) ? v.duration : 0,
      playing: !v.paused && !v.ended,
      muted: v.muted,
      ready: v.readyState >= 1,
    }), 500)
    const onErr = () => setError('media')
    v.addEventListener('error', onErr)
    return () => { clearInterval(timer); v.removeEventListener('error', onErr) }
  }, [src])

  const api = useMemo<PlayerApi>(() => ({
    toggle() { const v = ref.current; if (!v) return; v.paused ? v.play() : v.pause() },
    toggleMute() { const v = ref.current; if (v) v.muted = !v.muted },
    seek(frac) { const v = ref.current; if (v?.duration) v.currentTime = frac * v.duration },
  }), [])
  return { ref, api, error, ...st }
}

export function fmtMedia(s: number | null | undefined): string {
  const t = Math.max(0, Math.floor(s ?? 0))
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), sec = t % 60
  const mm = h ? String(m).padStart(2, '0') : String(m)
  return (h ? `${h}:` : '') + `${mm}:${String(sec).padStart(2, '0')}`
}

export function fmtClockEpoch(sec: number): string {
  const d = new Date(sec * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

/** Live captions show the local clock time of the captioned audio; videos show
 *  the position in the media. */
export function capTime(c: Caption, isLive: boolean): string {
  return isLive && c.captured_at ? fmtClockEpoch(c.captured_at) : c.time
}
