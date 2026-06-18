import { useEffect, useRef, useState } from 'react'
import type {
  Caption, ChatItem, ChatStatus, DownloadStatus, LibraryItem, PipelineStatus, SourceEvent, Status,
} from './types'

export function useBifrost() {
  const [connected, setConnected] = useState(false)
  const [status, setStatus] = useState<Status>({ state: 'idle' })
  const [source, setSource] = useState<SourceEvent | null>(null)
  const [captions, setCaptions] = useState<Caption[]>([])
  const [chat, setChat] = useState<ChatItem[]>([])
  const [chatStatus, setChatStatus] = useState<ChatStatus | null>(null)
  const [warning, setWarning] = useState<string | null>(null)
  const [download, setDownload] = useState<DownloadStatus | null>(null)
  const [libraryItems, setLibraryItems] = useState<LibraryItem[]>([])
  const [pipeline, setPipeline] = useState<PipelineStatus | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  // Survive backend restarts AND full page reloads: the active URL is persisted
  // to localStorage, so if the server comes back empty (status: idle) we restart
  // the same URL. For VODs the partial-transcript cache makes that near-instant;
  // for a live stream the backend replays its saved captions + chat (live.json).
  const lastUrlRef = useRef<string | null>(
    (() => { try { return localStorage.getItem('bf-active-url') } catch { return null } })()
  )
  const userStoppedRef = useRef(false)
  const setActiveUrl = (url: string | null) => {
    lastUrlRef.current = url
    try {
      if (url) localStorage.setItem('bf-active-url', url)
      else localStorage.removeItem('bf-active-url')
    } catch { /* private mode / storage disabled */ }
  }

  useEffect(() => {
    let alive = true
    let retry: ReturnType<typeof setTimeout>
    function connect() {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      const ws = new WebSocket(`${proto}://${location.host}/ws`)
      wsRef.current = ws
      ws.onopen = () => { setConnected(true); setCaptions([]); setChat([]) }
      ws.onclose = () => { setConnected(false); if (alive) retry = setTimeout(connect, 1500) }
      ws.onmessage = (e) => {
        const ev = JSON.parse(e.data)
        switch (ev.type) {
          case 'status':
            if (ev.state === 'idle' && lastUrlRef.current && !userStoppedRef.current) {
              // Backend restarted under us — restore the session.
              ws.send(JSON.stringify({ action: 'start', url: lastUrlRef.current }))
              break
            }
            setStatus(ev)
            if (ev.state === 'ended') setActiveUrl(null)  // don't resurrect a finished session
            if (ev.state === 'idle') {
              setSource(null); setCaptions([]); setChat([])
              setChatStatus(null); setWarning(null); setDownload(null); setPipeline(null)
            }
            break
          case 'source': setSource(ev); break
          case 'pipeline': setPipeline(ev); break
          case 'caption': setCaptions(c => [...c.slice(-299), ev]); break
          case 'chat': setChat(c => [...c, ...ev.items].slice(-300)); break
          case 'chat_status': setChatStatus(ev); break
          case 'warning': setWarning(ev.message); break
          case 'download_status': setDownload(ev); break
          case 'library': setLibraryItems(ev.items ?? []); break
        }
      }
    }
    connect()
    return () => { alive = false; clearTimeout(retry); wsRef.current?.close() }
  }, [])

  // Only send on an OPEN socket: sending while CONNECTING throws (and would
  // crash render). Callers that matter re-fire on `connected` change, so a
  // dropped early send is harmless.
  const send = (msg: object) => {
    const ws = wsRef.current
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg))
  }
  return {
    connected, status, source, captions, chat, chatStatus, warning, download, libraryItems, pipeline,
    start: (url: string) => {
      setActiveUrl(url)
      userStoppedRef.current = false
      setCaptions([]); setChat([])  // server replays authoritative history (cache / live restore)
      send({ action: 'start', url })
    },
    stop: () => {
      setActiveUrl(null)
      userStoppedRef.current = true
      send({ action: 'stop' })
    },
    setChatActive: (active: boolean) => send({ action: 'chat_active', active }),
    requestDownload: () => send({ action: 'download' }),
    regenerate: () => send({ action: 'regenerate' }),
    deleteItem: (videoId: string) => send({ action: 'delete_item', video_id: videoId }),
  }
}

export function usePref<T>(key: string, initial: T): [T, (v: T | ((p: T) => T)) => void] {
  const [val, setVal] = useState<T>(() => {
    try {
      const saved = JSON.parse(localStorage.getItem('bf-prefs') ?? '{}')
      return key in saved ? saved[key] : initial
    } catch { return initial }
  })
  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem('bf-prefs') ?? '{}')
      saved[key] = val
      localStorage.setItem('bf-prefs', JSON.stringify(saved))
    } catch { /* ignore */ }
  }, [key, val])
  return [val, setVal]
}

/** Ticking local time, for eyeballing caption lag. */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(t)
  }, [intervalMs])
  return now
}
