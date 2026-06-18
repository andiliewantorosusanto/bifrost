import { useEffect, useRef } from 'react'
import type { Caption } from '../types'
import { capTime } from '../players'
import { CaptionView } from './Caption'

export function Transcript({ captions, dual, isLive }: {
  captions: Caption[]
  dual: boolean
  isLive: boolean
}) {
  const listRef = useRef<HTMLDivElement>(null)
  const stickRef = useRef(true) // follow new lines only while at the bottom

  function onScroll() {
    const el = listRef.current
    if (el) stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60
  }
  useEffect(() => {
    const el = listRef.current
    if (el && stickRef.current) el.scrollTop = el.scrollHeight
  }, [captions.length, dual])

  return (
    <div className="mt-5 rounded-xl border border-line bg-ink-900 px-4.5 py-4">
      <h3 className="mb-3 text-[11px] font-semibold uppercase tracking-[0.12em] text-fg-faint">Transcript</h3>
      <div ref={listRef} onScroll={onScroll} className="max-h-[340px] overflow-y-auto overscroll-contain pr-1">
        {captions.length === 0 && (
          <span className="text-sm text-fg-faint">
            English transcript appears here as each chunk is processed.
          </span>
        )}
        {captions.map(c => (
          <div key={c.id} className="border-t border-line py-3 first:border-t-0 first:pt-0 last:pb-0">
            <CaptionView time={capTime(c, isLive)} text={c.text} original={dual ? c.original : undefined} />
          </div>
        ))}
      </div>
    </div>
  )
}
