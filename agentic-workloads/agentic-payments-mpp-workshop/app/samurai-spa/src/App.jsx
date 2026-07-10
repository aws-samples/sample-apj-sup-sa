import { useState } from 'react'
import { Authenticator } from '@aws-amplify/ui-react'
import { signOut } from 'aws-amplify/auth'
import AgentRunner from './components/AgentRunner.jsx'
import DebugPanel from './components/DebugPanel.jsx'

export default function App() {
  const [debugOpen, setDebugOpen] = useState(true)
  const [inFlight, setInFlight] = useState(false)

  return (
    <Authenticator hideSignUp loginMechanisms={['email']}>
      {({ user }) => (
        <div className="min-h-screen w-full flex">
          <div className="flex-1 min-w-0 flex flex-col">
            <header className="flex items-center justify-between px-6 py-4 border-b border-black/10 bg-white">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-[#635bff] flex items-center justify-center text-white text-sm font-bold">
                  S
                </div>
                <div>
                  <div className="font-semibold">Samurai</div>
                  <div className="text-xs text-[#7a7a7a]">An agent that pays other agents (MPP demo)</div>
                </div>
              </div>
              <div className="flex items-center gap-3">
                <span className="text-xs text-[#7a7a7a]">{user?.username}</span>
                <button
                  onClick={() => setDebugOpen((o) => !o)}
                  className={`text-sm px-3 py-1.5 rounded-md border ${
                    debugOpen ? 'bg-[#635bff] text-white border-[#635bff]' : 'bg-white border-black/10'
                  }`}
                >
                  {debugOpen ? 'Hide MPP panel' : 'Show MPP panel'}
                </button>
                <button
                  onClick={() => signOut()}
                  className="text-sm px-3 py-1.5 rounded-md border border-black/10 bg-white"
                >
                  Sign out
                </button>
              </div>
            </header>
            <main className="flex-1 min-h-0 overflow-hidden">
              <AgentRunner onInFlightChange={setInFlight} />
            </main>
          </div>

          {debugOpen && (
            <aside className="w-[480px] border-l border-black/10 bg-white flex flex-col min-h-0">
              <DebugPanel inFlight={inFlight} />
            </aside>
          )}
        </div>
      )}
    </Authenticator>
  )
}
