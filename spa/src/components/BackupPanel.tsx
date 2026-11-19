import { useState } from 'react'
import { call } from '../api/client'
import { ErrorBanner, useAction } from './common'

type Snapshot = Record<string, unknown>
type Validation = { valid: boolean; errors: { code: string; path: string; message: string }[]; warnings: { code: string; path: string; message: string }[] }

export function ConfigPanel() {
  const [candidate, setCandidate] = useState('')
  const [message, setMessage] = useState('')
  const [validation, setValidation] = useState<Validation | null>(null)
  const action = useAction()

  const save = (download: boolean) => action.run(async () => {
    const result = await call<{ snapshot: Snapshot; managed: boolean }>('opal_config_save', {
      body: { destination: download ? 'response' : 'managed' },
    })
    if (download) {
      const url = URL.createObjectURL(new Blob([JSON.stringify(result.snapshot, null, 2)], { type: 'application/json' }))
      const link = document.createElement('a')
      link.href = url
      link.download = 'opal-say-config.json'
      link.click()
      URL.revokeObjectURL(url)
    }
    setMessage(download ? 'Snapshot downloaded; managed snapshot unchanged.' : 'Managed snapshot saved.')
  })

  const load = (fromCandidate: boolean) => action.run(async () => {
    if (!window.confirm('Replace the complete live configuration with this snapshot?')) return
    await call('opal_config_load', { body: fromCandidate ? { snapshot: JSON.parse(candidate) } : {} })
    setMessage('Configuration loaded. The managed snapshot was not changed.')
  })

  const validate = (fromCandidate: boolean) => action.run(async () => {
    setValidation(await call<Validation>('opal_config_validate', { body: fromCandidate ? { snapshot: JSON.parse(candidate) } : {} }))
  })

  return (
    <section>
      <h2>Configuration snapshots</h2>
      <p>Snapshots replace settings overrides and declarative resources, not media, history, credentials or models.</p>
      <div className="row">
        <button onClick={() => void save(false)}>Save managed snapshot</button>
        <button onClick={() => void save(true)}>Download snapshot</button>
        <button onClick={() => void load(false)}>Load managed snapshot</button>
        <button onClick={() => void validate(false)}>Validate live configuration</button>
      </div>
      <label>
        Candidate snapshot (JSON)
        <textarea value={candidate} rows={10} onChange={(event) => setCandidate(event.target.value)} />
      </label>
      <input aria-label="Open snapshot JSON" type="file" accept=".json,application/json" onChange={(event) => {
        const file = event.target.files?.[0]
        if (file) void action.run(async () => setCandidate(await file.text()))
      }} />
      <div className="row">
        <button disabled={!candidate} onClick={() => void validate(true)}>Validate candidate</button>
        <button disabled={!candidate} onClick={() => void load(true)}>Load candidate</button>
      </div>
      {message && <p role="status">{message}</p>}
      <ErrorBanner error={action.error} />
      {validation && <pre data-testid="snapshot-validation">{JSON.stringify(validation, null, 2)}</pre>}
    </section>
  )
}
