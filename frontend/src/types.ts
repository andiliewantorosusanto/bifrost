export interface SourceEvent {
  type: 'source'
  video_id: string
  title: string
  channel: string
  is_live: boolean
  live_status: string
  duration: number | null
  chunk_seconds: number
  model: string
  media?: string | null
  cached?: boolean
}

export interface Caption {
  type: 'caption'
  id: number
  time: string
  t0: number
  t1: number
  captured_at?: number
  text: string
  original?: string | null
  non_english?: boolean
}

export interface ChatItem {
  author: string
  mod: boolean
  text: string
  original?: string | null
  src: string
  published_at?: string
}

export interface Status {
  state: 'idle' | 'probing' | 'running' | 'ended' | 'error'
  message?: string
}

export interface ChatStatus {
  ok: boolean
  message: string
}

export interface DownloadStatus {
  state: 'downloading' | 'done' | 'error'
  progress?: number
  message?: string
}

export interface LibraryItem {
  video_id: string
  title: string
  channel: string
  duration: number | null
  saved_at: number | null
  has_media: boolean
  has_captions: boolean
}

export interface PipelineStatus {
  captured_s: number
  target_s: number | null
  speed: number | null
  whisper_ms: number | null
  local?: boolean
}

export type CapSize = 'sm' | 'md' | 'lg' | 'xl'
