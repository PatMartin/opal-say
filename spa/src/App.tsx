import { useState } from 'react'
import { call } from './api/client'
import type { ServiceInfo } from './api/types'
import { BackupPanel } from './components/BackupPanel'
import { useLoad } from './components/common'
import { EnginesPanel } from './components/EnginesPanel'
import { GeneratePanel } from './components/GeneratePanel'
import { HealthPanel } from './components/HealthPanel'
import { PersonasPanel } from './components/PersonasPanel'
import { ProfilesPanel } from './components/ProfilesPanel'
import { SettingsPanel } from './components/SettingsPanel'
import { VoicesPanel } from './components/VoicesPanel'

const TABS = {
  Generate: GeneratePanel,
  Voices: VoicesPanel,
  Profiles: ProfilesPanel,
  Personas: PersonasPanel,
  Engines: EnginesPanel,
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
