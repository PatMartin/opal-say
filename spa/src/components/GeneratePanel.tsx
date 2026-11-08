import { useState, type FormEvent } from 'react'
import { audioMeta, call, callRaw } from '../api/client'
import type { Engine, Persona, Profile, Voice } from '../api/types'
import { concatChunks, pcmToWav } from '../audio'
import { AudioResult, ErrorBanner, OptionsField, parseOptions, useAction, useLoad, type AudioClip } from './common'

/** Completed (/generate) and streamed (/generate/stream) speech generation. */
export function GeneratePanel() {
  const profile = useLoad(() => call<Profile[]>('opal_profile_list'), [])
  const voice = useLoad(() => call<Voice[]>('opal_voice_list', { query: { enabled: true } }), [])
  const engine = useLoad(() => call<Engine[]>('opal_engine_list'), [])
  const persona = useLoad(() => call<Persona[]>('opal_persona_list', { query: { enabled: true } }), [])

  const [text, setText] = useState('')
  const [profileId, setProfileId] = useState('')
  const [personaId, setPersonaId] = useState('')
  const [voiceId, setVoiceId] = useState('')
  const [engineId, setEngineId] = useState('')
  const [speed, setSpeed] = useState('')
  const [volume, setVolume] = useState('')
  const [format, setFormat] = useState('')
  const [sampleRate, setSampleRate] = useState('')
  const [options, setOptions] = useState('')
  const [stream, setStream] = useState(false)
  const [received, setReceived] = useState<number | null>(null)
  const [clip, setClip] = useState<AudioClip | null>(null)
  const action = useAction()

  const submit = (e: FormEvent) => {
    e.preventDefault()
    void action.run(async () => {
      setClip(null)
      setReceived(null)
      const body: Record<string, unknown> = { text }
      if (profileId) body.profile_id = Number(profileId)
      if (personaId) body.persona = personaId
      if (voiceId) body.voice_id = voiceId
      if (engineId) body.engine_id = engineId
      if (speed) body.speed = Number(speed)
      if (volume) body.volume = Number(volume)
      if (sampleRate) body.sample_rate = Number(sampleRate)
      const engineOptions = parseOptions(options)
      if (engineOptions) body.engine_options = engineOptions

      if (!stream) {
        if (format) body.output_format = format
        const resp = await callRaw('opal_generate', { body })
        const meta = audioMeta(resp)
        const raw = new Uint8Array(await resp.arrayBuffer())
        const blob = meta.format === 'pcm' ? pcmToWav(raw, meta.sampleRate) : new Blob([raw], { type: 'audio/wav' })
        setClip({ url: URL.createObjectURL(blob), meta, bytes: raw.byteLength, filename: `opal-say.${meta.format}` })
        return
      }

      const resp = await callRaw('opal_generate_stream', { body })
      const meta = audioMeta(resp)
      const chunks: Uint8Array[] = []
      let total = 0
      const reader = resp.body!.getReader()
      try {
        for (;;) {
          const { done, value } = await reader.read()
          if (done) break
          chunks.push(value)
          total += value.byteLength
          setReceived(total)
        }
      } catch (err) {
        throw new Error(`stream terminated prematurely after ${total} bytes; generation failed (${String(err)})`)
      }
      const pcm = concatChunks(chunks)
      setClip({ url: URL.createObjectURL(pcmToWav(pcm, meta.sampleRate)), meta, bytes: pcm.byteLength, filename: 'opal-say-stream.wav' })
    })
  }

  return (
    <section>
      <h2>Generate</h2>
      <ErrorBanner error={profile.error || voice.error || engine.error} />
      <form onSubmit={submit} className="grid">
        <label className="wide">
          Text
          <textarea rows={4} required value={text} onChange={(e) => setText(e.target.value)} />
        </label>
        <label>
          Profile
          <select value={profileId} onChange={(e) => setProfileId(e.target.value)}>
            <option value="">(active profile)</option>
            {profile.data.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
                {p.is_active ? ' (active)' : ''}
              </option>
            ))}
          </select>
        </label>
        <label>
          Persona
          <select value={personaId} onChange={(e) => setPersonaId(e.target.value)}>
            <option value="">(none)</option>
            {persona.data.map((p) => (
              <option key={p.persona_id} value={p.persona_id}>
                {p.name} ({p.voice_id})
              </option>
            ))}
          </select>
        </label>
        <label>
          Voice
          <select value={voiceId} onChange={(e) => setVoiceId(e.target.value)}>
            <option value="">(from profile)</option>
            {voice.data.map((v) => (
              <option key={v.voice_id} value={v.voice_id}>
                {v.name} ({v.voice_id})
              </option>
            ))}
          </select>
        </label>
        <label>
          Engine override
          <select value={engineId} onChange={(e) => setEngineId(e.target.value)}>
            <option value="">(voice engine)</option>
            {engine.data.map((en) => (
              <option key={en.engine_id} value={en.engine_id}>
                {en.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Speed
          <input type="number" step="0.05" min="0.25" max="4" value={speed} placeholder="default" onChange={(e) => setSpeed(e.target.value)} />
        </label>
        <label>
          Volume
          <input type="number" step="0.05" min="0" max="2" value={volume} placeholder="default" onChange={(e) => setVolume(e.target.value)} />
        </label>
        <label>
          Sample rate (Hz)
          <input type="number" min="8000" max="192000" value={sampleRate} placeholder="native" onChange={(e) => setSampleRate(e.target.value)} />
        </label>
        <label>
          Format
          <select value={format} disabled={stream} onChange={(e) => setFormat(e.target.value)}>
            <option value="">(default)</option>
            <option value="wav">wav</option>
            <option value="pcm">pcm</option>
          </select>
        </label>
        <label className="check">
          <input type="checkbox" checked={stream} onChange={(e) => setStream(e.target.checked)} /> Stream (raw PCM)
        </label>
        <OptionsField value={options} onChange={setOptions} />
        <div className="wide">
          <button type="submit" disabled={action.busy}>
            {action.busy ? 'Generating…' : 'Generate audio'}
          </button>
          {received !== null && <span className="muted"> received {received} bytes</span>}
        </div>
      </form>
      <ErrorBanner error={action.error} />
      <AudioResult clip={clip} />
    </section>
  )
}
