import { useEffect } from 'react'
import { useBifrost, usePref } from './useBifrost'
import type { CapSize } from './types'
import { ConnectScreen } from './components/ConnectScreen'
import { TopBar } from './components/TopBar'
import { Stage } from './components/Stage'
import { Transcript } from './components/Transcript'
import { ChatPanel } from './components/ChatPanel'

function WarningBar({ message }: { message: string }) {
  return (
    <div className="border-b border-line bg-warn/10 px-4.5 py-2 text-sm text-warn">
      {message}
    </div>
  )
}

export default function App() {
  const bf = useBifrost()
  const [dual, setDual] = usePref('dual', true)
  const [overlayOn, setOverlayOn] = usePref('overlay', true)
  const [capSize, setCapSize] = usePref<CapSize>('capSize', 'md')
  const [chatOpen, setChatOpen] = usePref('chatOpen', true)
  const inViewer = bf.source && (bf.status.state === 'running' || bf.status.state === 'ended')

  // Tell the backend to poll live chat only while its panel is open AND the tab
  // is visible — each poll costs API quota whether or not anyone is chatting.
  useEffect(() => {
    const sync = () => bf.setChatActive(chatOpen && document.visibilityState === 'visible')
    sync()
    document.addEventListener('visibilitychange', sync)
    return () => document.removeEventListener('visibilitychange', sync)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatOpen, bf.connected, bf.source?.is_live])

  if (!inViewer || !bf.source) {
    return (
      <div className="flex h-full flex-col">
        <ConnectScreen onStart={bf.start} status={bf.status}
          connected={bf.connected} libraryItems={bf.libraryItems} onDelete={bf.deleteItem} />
      </div>
    )
  }
  return (
    <div className="flex h-full flex-col">
      <TopBar source={bf.source} status={bf.status} onExit={bf.stop} />
      {bf.warning && <WarningBar message={bf.warning} />}
      <div className="flex min-h-0 flex-1">
        <div className="flex-1 overflow-y-auto px-6 py-6">
          <div className="mx-auto w-full max-w-[940px]">
            <Stage source={bf.source} captions={bf.captions} status={bf.status}
              dual={dual} setDual={setDual} overlayOn={overlayOn} setOverlayOn={setOverlayOn}
              capSize={capSize} setCapSize={setCapSize}
              download={bf.download} onDownload={bf.requestDownload} onRegenerate={bf.regenerate}
              pipeline={bf.pipeline} />
            <Transcript captions={bf.captions} dual={dual} isLive={bf.source.is_live} />
          </div>
        </div>
        <div className={`${chatOpen ? 'w-[360px]' : 'w-11'} shrink-0 max-[920px]:hidden`}>
          <ChatPanel items={bf.chat} chatStatus={bf.chatStatus} isLive={bf.source.is_live}
            open={chatOpen} onToggle={() => setChatOpen(o => !o)} />
        </div>
      </div>
    </div>
  )
}
