import { useState } from 'react'
import { call, setBearerToken } from './api/client'
import type { Health } from './api/types'
import { ConfigPanel } from './components/BackupPanel'
import { useLoad } from './components/common'
import { EnginePanel } from './components/EnginePanel'
import { GeneratePanel } from './components/GeneratePanel'
import { HealthPanel } from './components/HealthPanel'
import { PersonaPanel } from './components/PersonaPanel'
import { ProfilePanel } from './components/ProfilePanel'
import { ServicePanel } from './components/ServicePanel'
import { SettingPanel } from './components/SettingPanel'
import { VoicePanel } from './components/VoicePanel'

const TABS = {
  Generate: GeneratePanel,
  Voice: VoicePanel,
  Profile: ProfilePanel,
  Persona: PersonaPanel,
  Engine: EnginePanel,
  Setting: SettingPanel,
  Configuration: ConfigPanel,
  Health: HealthPanel,
  Service: ServicePanel,
} as const
type Tab = keyof typeof TABS

export function App() {
  const [tab, setTab] = useState<Tab>('Generate')
  const [authRevision, setAuthRevision] = useState(0)
  const health = useLoad(() => call<Health | null>('opal_health_get'), null)
  const live = useLoad(() => call<{ status: string } | null>('opal_health_live_get'), null)
  const Panel = TABS[tab]
  return (
    <div className="app">
      <header>
        <h1>OPAL Say</h1>
        <span className="muted" data-testid="service-health">
          {health.data ? `${health.data.name} ${health.data.version} (API ${health.data.api_version})` : 'connecting…'}
        </span>
        <span className={live.data?.status === 'ok' ? 'pill ok' : 'pill error'}>{live.data?.status === 'ok' ? 'online' : 'offline'}</span>
      </header>
      <form className="row" onSubmit={(event) => {
        event.preventDefault()
        const form = event.currentTarget
        setBearerToken(String(new FormData(form).get('credential') ?? ''))
        form.reset()
        setAuthRevision((revision) => revision + 1)
        void health.reload()
        void live.reload()
      }}>
        <label>Bearer credential <input name="credential" type="password" autoComplete="off" /></label>
        <button type="submit">Use credential</button>
      </form>
      <nav>
        {(Object.keys(TABS) as Tab[]).map((t) => (
          <button key={t} className={t === tab ? 'active' : ''} onClick={() => setTab(t)}>
            {t}
          </button>
        ))}
      </nav>
      <main>
        <Panel key={authRevision} />
      </main>
    </div>
  )
}
