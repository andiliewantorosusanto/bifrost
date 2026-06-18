import type { CapSize } from '../types'

export const CAP_SIZES: Record<CapSize, { primary: string; original: string; pad: string }> = {
  sm: { primary: 'text-[13px]', original: 'text-[11px]', pad: 'px-3 py-2' },
  md: { primary: 'text-base', original: 'text-[12.5px]', pad: 'px-3.5 py-2.5' },
  lg: { primary: 'text-[21px]', original: 'text-sm', pad: 'px-4 py-3' },
  xl: { primary: 'text-[27px]', original: 'text-base', pad: 'px-5 py-3.5' },
}

export function CaptionView({ text, original, time, live = false, overlay = false, size = 'md' }: {
  text: string
  original?: string | null
  time?: string
  live?: boolean
  overlay?: boolean
  size?: CapSize
}) {
  const s = CAP_SIZES[size]
  return (
    <div className={overlay
      ? `max-w-[760px] rounded-lg border border-white/10 bg-ink-950/80 ${s.pad} text-center backdrop-blur-md`
      : 'max-w-[760px]'}>
      {time && (
        <div className={`mb-1 flex items-center gap-2 font-mono text-[11px] text-fg-faint ${overlay ? 'justify-center' : ''}`}>
          {time}
        </div>
      )}
      <div className={`${overlay ? s.primary : 'text-[17px]'} font-medium leading-snug text-white [text-wrap:pretty]`}>
        {text}
        {live && <span className="ml-1 inline-block h-[1em] w-0.5 animate-pulse bg-accent-400 align-[-2px]" />}
      </div>
      {original && (
        <div className={`mt-1 ${overlay ? s.original : 'text-sm'} leading-normal text-fg-faint [text-wrap:pretty]`}>
          {original}
        </div>
      )}
    </div>
  )
}
