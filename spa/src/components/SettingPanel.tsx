import { useState, type FormEvent } from 'react'
import { call } from '../api/client'
import type { Setting } from '../api/types'
import { ErrorBanner, useAction, useLoad } from './common'

// Setting values are native JSON: `5`, `true`, {"a": 1} send as typed values;
// anything that doesn't parse is sent as a plain string.
function parseValue(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

function formatValue(v: unknown): string {
  return typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v)
}

export function SettingPanel() {
  const setting = useLoad(() => call<Record<string, unknown>>('opal_setting_list'), {})
  const [key, setKey] = useState('')
  const [value, setValue] = useState('')
  const [detail, setDetail] = useState<Setting | null>(null)
  const action = useAction()

  const save = (e: FormEvent) => {
    e.preventDefault()
    void action.run(async () => {
      setDetail(await call<Setting>('opal_setting_put', { path: { key }, body: { value: parseValue(value) } }))
      setKey('')
      setValue('')
      await setting.reload()
    })
  }

  const show = (k: string) =>
    action.run(async () => setDetail(await call<Setting>('opal_setting_get', { path: { key: k } })))

  const remove = (k: string) =>
    action.run(async () => {
      await call('opal_setting_delete', { path: { key: k } })
      await setting.reload()
    })

  return (
    <section>
      <h2>Setting</h2>
      <p className="muted">
        Known keys: max_text_chars, max_audio_bytes, chunk_max_chars, default_speed, default_volume, default_output_format. Values are native JSON (numbers, booleans, objects). Deleting a setting restores its default.
        <button
          onClick={() =>
            void action.run(async () => {
              await call('opal_setting_patch', {
                body: {
                  max_text_chars: 20000,
                  max_audio_bytes: 134217728,
                  chunk_max_chars: 400,
                  default_speed: 1,
                  default_volume: 1,
                  default_output_format: 'wav',
                },
              })
              await setting.reload()
            })
          }
        >
          Reset to defaults
        </button>
      </p>
      <ErrorBanner error={setting.error} />
      <table>
        <thead>
          <tr>
            <th>Key</th>
            <th>Value</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {Object.entries(setting.data).map(([k, v]) => (
            <tr key={k}>
              <td>{k}</td>
              <td className="mono">{formatValue(v)}</td>
              <td className="actions">
                <button onClick={() => (setKey(k), setValue(formatValue(v)))}>Edit</button>
                <button onClick={() => void show(k)}>Details</button>
                <button onClick={() => void remove(k)}>Delete</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <ErrorBanner error={action.error} />
      {detail && (
        <p className="muted" data-testid="setting-detail">
          #{detail.id} {detail.key} = {formatValue(detail.value)} (updated {detail.updated_at})
        </p>
      )}
      <form onSubmit={save} className="row">
        <label>
          Key
          <input required value={key} onChange={(e) => setKey(e.target.value)} />
        </label>
        <label>
          Value
          <input value={value} onChange={(e) => setValue(e.target.value)} />
        </label>
        <button type="submit">Set</button>
      </form>
    </section>
  )
}
