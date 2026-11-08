import { useState, type FormEvent } from 'react'
import { call } from '../api/client'
import type { Persona, Voice } from '../api/types'
import { ErrorBanner, useAction, useLoad } from './common'

interface PersonaForm {
  persona_id: string
  name: string
  description: string
  voice_id: string
  enabled: boolean
}

const EMPTY: PersonaForm = { persona_id: '', name: '', description: '', voice_id: '', enabled: true }

export function PersonasPanel() {
  const personas = useLoad(() => call<Persona[]>('list_personas_api_v1_personas__get'), [])
  const voices = useLoad(() => call<Voice[]>('list_voices_api_v1_voices__get'), [])
  const [form, setForm] = useState<PersonaForm>(EMPTY)
  const [editing, setEditing] = useState<string | null>(null)
  const [selected, setSelected] = useState<Persona | null>(null)
  const action = useAction()
  const set = (patch: Partial<PersonaForm>) => setForm((f) => ({ ...f, ...patch }))

  const submit = (e: FormEvent) => {
    e.preventDefault()
    void action.run(async () => {
      const body = {
        name: form.name,
        description: form.description || null,
        voice_id: form.voice_id,
        enabled: form.enabled,
      }
      if (editing !== null) {
        await call('update_persona_api_v1_personas__persona_id__patch', { path: { persona_id: editing }, body })
      } else {
        await call('create_persona_api_v1_personas__post', { body: { persona_id: form.persona_id, ...body } })
      }
      setForm(EMPTY)
      setEditing(null)
      await personas.reload()
    })
  }

  const edit = (p: Persona) => {
    setEditing(p.persona_id)
    setForm({
      persona_id: p.persona_id,
      name: p.name,
      description: p.description ?? '',
      voice_id: p.voice_id,
      enabled: p.enabled,
    })
  }

  const show = (id: string) =>
    action.run(async () => setSelected(await call<Persona>('get_persona_api_v1_personas__persona_id__get', { path: { persona_id: id } })))

  const remove = (id: string) =>
    action.run(async () => {
      await call('delete_persona_api_v1_personas__persona_id__delete', { path: { persona_id: id } })
      await personas.reload()
    })

  return (
    <section>
      <h2>Personas</h2>
      <ErrorBanner error={personas.error} />
      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>Name</th>
            <th>Voice</th>
            <th>Enabled</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {personas.data.map((p) => (
            <tr key={p.persona_id}>
              <td>{p.persona_id}</td>
              <td>{p.name}</td>
              <td>{p.voice_id}</td>
              <td>{p.enabled ? 'yes' : 'no'}</td>
              <td className="actions">
                <button onClick={() => void show(p.persona_id)}>Details</button>
                <button onClick={() => edit(p)}>Edit</button>
                <button onClick={() => void remove(p.persona_id)}>Delete</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <ErrorBanner error={action.error} />
      {selected && <pre data-testid="persona-details">{JSON.stringify(selected, null, 2)}</pre>}

      <h3>{editing !== null ? `Edit persona ${editing}` : 'Create persona'}</h3>
      <form onSubmit={submit} className="grid">
        <label>
          Persona ID
          <input required disabled={editing !== null} value={form.persona_id}
                 onChange={(e) => set({ persona_id: e.target.value })} />
        </label>
        <label>
          Name
          <input required value={form.name} onChange={(e) => set({ name: e.target.value })} />
        </label>
        <label>
          Voice
          <select required value={form.voice_id} onChange={(e) => set({ voice_id: e.target.value })}>
            <option value="">choose…</option>
            {voices.data.map((v) => (
              <option key={v.voice_id} value={v.voice_id}>
                {v.name} ({v.voice_id})
              </option>
            ))}
          </select>
        </label>
        <label>
          Description
          <input value={form.description} onChange={(e) => set({ description: e.target.value })} />
        </label>
        <label className="check">
          <input type="checkbox" checked={form.enabled} onChange={(e) => set({ enabled: e.target.checked })} /> Enabled
        </label>
        <div className="wide">
          <button type="submit">{editing !== null ? 'Save' : 'Create'}</button>
          {editing !== null && (
            <button type="button" onClick={() => (setEditing(null), setForm(EMPTY))}>
              Cancel
            </button>
          )}
        </div>
      </form>
    </section>
  )
}
