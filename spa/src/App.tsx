import { useState } from 'react'
import { call } from './api/client'
import type { ServiceInfo } from './api/types'
import { BackupPanel } from './components/BackupPanel'
import { useLoad } from './components/common'
import { EnginePanel } from './components/EnginePanel'
import { GeneratePanel } from './components/GeneratePanel'
import { HealthPanel } from './components/HealthPanel'
import { PersonaPanel } from './components/PersonaPanel'
import { ProfilesPanel } from './components/ProfilesPanel'
import { SettingsPanel } from './components/SettingsPanel'
import { VoicePanel } from './components/VoicePanel'

const TABS = {
  Generate: GeneratePanel,
  Voice: VoicePanel,
  Profiles: ProfilesPanel,
  Persona: PersonaPanel,
  Engine: EnginePanel,
  Settings: SettingsPanel,
  Backup: BackupPanel,
  Health: HealthPanel,
} as const
type Tab = keyof typeof TABS

export function App() {
  const [tab, setTab] = useState<Tab>('Generate')
  const info = useLoad(() => call<ServiceInfo | null>('opal_info_get'), null)
  const live = useLoad(() => call<{ status: string } | null>('opal_info_ping'), null)
  const Panel = TABS[tab]
  return (
    <div className="app">
      <header>
        <h1>OPAL Say</h1>
        <span className="muted" data-testid="service-info">
          {info.data ? `${info.data.name} ${info.data.version} (API ${info.data.api_version})` : 'connecting…'}
        </span>
        <span className={live.data?.status === 'ok' ? 'pill ok' : 'pill error'}>{live.data?.status === 'ok' ? 'online' : 'offline'}</span>
      </header>
      <nav>
        {(Object.keys(TABS) as Tab[]).map((t) => (
          <button key={t} className={t === tab ? 'active' : ''} onClick={() => setTab(t)}>
            {t}
          </button>
        ))}
      </nav>
      <main>
        <Panel />
      </main>
    </div>
  )
}
