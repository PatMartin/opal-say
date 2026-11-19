import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { App } from '../App'
import { pcmToWav } from '../audio'
import { AUDIO_HEADERS, ENGINE, PROFILE, VOICE, audio, json, mockApi } from './mockApi'

const BASE_ROUTES = {
  'GET /api/v1/health/': { name: 'opal-say-svc', version: '0.1.0', api_version: 'v1', live: true, ready: true, details: { engines: [] } },
  'GET /api/v1/health/live': { status: 'ok' },
  'GET /api/v1/setting/': { chunk_max_chars: 400 },
  'GET /api/v1/profile/': [PROFILE],
  'GET /api/v1/voice/': [VOICE],
  'GET /api/v1/engine/': [ENGINE],
}

async function openTab(name: string) {
  await userEvent.click(screen.getByRole('button', { name }))
}

describe('App shell', () => {
  it('shows service identity and liveness', async () => {
    mockApi(BASE_ROUTES)
    render(<App />)
    expect(await screen.findByText('opal-say-svc 0.1.0 (API v1)')).toBeInTheDocument()
    expect(screen.getByText('online')).toBeInTheDocument()
  })
})

describe('Generate', () => {
  it('generates completed WAV with request overrides', async () => {
    const calls = mockApi({
      ...BASE_ROUTES,
      'POST /api/v1/generate': () => audio(new Uint8Array(100), { ...AUDIO_HEADERS, 'X-Opal-Profile-Id': '1' }),
    })
    render(<App />)
    await userEvent.type(screen.getByLabelText('Text'), 'Hello world')
    await screen.findByRole('option', { name: 'Amy (amy)' })
    await userEvent.selectOptions(screen.getByLabelText('Voice'), 'amy')
    await userEvent.type(screen.getByLabelText('Sample rate (Hz)'), '16000')
    await userEvent.selectOptions(screen.getByLabelText('Format'), 'wav')
    await userEvent.type(screen.getByLabelText('Engine options (JSON)'), '{{"noise_scale": 0.5}')
    await userEvent.click(screen.getByRole('button', { name: 'Generate audio' }))

    const result = await screen.findByTestId('audio-result')
    expect(result).toHaveTextContent('100 bytes · wav · 22050 Hz · voice amy · engine piper · profile 1')
    const post = calls.find((c) => c.path === '/api/v1/generate')!
    expect(post.body).toEqual({
      text: 'Hello world',
      voice_id: 'amy',
      sample_rate: 16000,
      output_format: 'wav',
      engine_options: { noise_scale: 0.5 },
    })
  })

  it('streams raw PCM progressively', async () => {
    const encoder = (n: number) => new Uint8Array(n)
    const calls = mockApi({
      ...BASE_ROUTES,
      'POST /api/v1/generate/stream': () =>
        new Response(
          new ReadableStream({
            start(controller) {
              controller.enqueue(encoder(200))
              controller.enqueue(encoder(300))
              controller.close()
            },
          }),
          { status: 200, headers: { ...AUDIO_HEADERS, 'X-Opal-Audio-Format': 'pcm', 'Content-Type': 'application/octet-stream' } },
        ),
    })
    render(<App />)
    await userEvent.type(screen.getByLabelText('Text'), 'Stream me')
    await userEvent.click(screen.getByLabelText(/Stream/))
    expect(screen.getByLabelText('Format')).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Generate audio' }))
    expect(await screen.findByText('received 500 bytes')).toBeInTheDocument()
    expect(await screen.findByTestId('audio-result')).toHaveTextContent('500 bytes · pcm · 22050 Hz')
    const post = calls.find((c) => c.path === '/api/v1/generate/stream')!
    expect(post.body).toEqual({ text: 'Stream me' })
  })

  it('reports premature stream termination as a failure', async () => {
    mockApi({
      ...BASE_ROUTES,
      'POST /api/v1/generate/stream': () =>
        new Response(
          new ReadableStream({
            start(controller) {
              controller.enqueue(new Uint8Array(10))
              controller.error(new Error('connection reset'))
            },
          }),
          { status: 200, headers: { ...AUDIO_HEADERS, 'X-Opal-Audio-Format': 'pcm' } },
        ),
    })
    render(<App />)
    await userEvent.type(screen.getByLabelText('Text'), 'x')
    await userEvent.click(screen.getByLabelText(/Stream/))
    await userEvent.click(screen.getByRole('button', { name: 'Generate audio' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('stream terminated prematurely')
  })

  it('shows contract error responses', async () => {
    mockApi({
      ...BASE_ROUTES,
      'POST /api/v1/generate': () => json({ code: 'no_voice', message: 'no voice could be resolved' }, 409),
    })
    render(<App />)
    await userEvent.type(screen.getByLabelText('Text'), 'x')
    await userEvent.click(screen.getByRole('button', { name: 'Generate audio' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('409 no_voice: no voice could be resolved')
  })

  it('shows 422 field errors', async () => {
    mockApi({
      ...BASE_ROUTES,
      'POST /api/v1/generate': () =>
        json({ detail: [{ loc: ['body', 'engine_options', 'bogus'], msg: 'unsupported option', type: 'x' }] }, 422),
    })
    render(<App />)
    await userEvent.type(screen.getByLabelText('Text'), 'x')
    await userEvent.click(screen.getByRole('button', { name: 'Generate audio' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('body.engine_options.bogus: unsupported option')
  })
})

describe('Voice', () => {
  it('registers, validates and unregisters a voice', async () => {
    const calls = mockApi({
      ...BASE_ROUTES,
      'POST /api/v1/voice/': () => json({ ...VOICE, voice_id: 'new' }, 201),
      'POST /api/v1/voice/amy/validate': { valid: false, errors: [{ code: 'model_not_found', message: 'missing', field: 'engine_voice_ref' }], warnings: [], details: {} },
      'DELETE /api/v1/voice/amy': () => json(null, 204),
    })
    render(<App />)
    await openTab('Voice')
    await screen.findByText('en_US-amy-low')
    await userEvent.type(screen.getByLabelText('Voice ID'), 'new')
    await userEvent.type(screen.getByLabelText('Name'), 'New voice')
    await userEvent.selectOptions(screen.getByLabelText('Engine', { selector: 'form select' }), 'piper')
    await userEvent.type(screen.getByLabelText('Engine voice reference'), '/models/new.onnx')
    await userEvent.click(screen.getByRole('button', { name: 'Register' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.path === '/api/v1/voice/')).toBe(true))
    expect(calls.find((c) => c.method === 'POST' && c.path === '/api/v1/voice/')!.body).toEqual({
      voice_id: 'new',
      name: 'New voice',
      engine_id: 'piper',
      engine_voice_ref: '/models/new.onnx',
      enabled: true,
      description: null,
      language: null,
      engine_options: {},
    })

    const row = screen.getByText('en_US-amy-low').closest('tr')!
    await userEvent.click(within(row).getByRole('button', { name: 'Validate' }))
    expect(await screen.findByTestId('validation')).toHaveTextContent('error: model_not_found [engine_voice_ref]: missing')
    await userEvent.click(within(row).getByRole('button', { name: 'Unregister' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE' && c.path === '/api/v1/voice/amy')).toBe(true))
  })

  it('filters by engine and enabled state', async () => {
    const calls = mockApi(BASE_ROUTES)
    render(<App />)
    await openTab('Voice')
    await screen.findByText('en_US-amy-low')
    await userEvent.selectOptions(screen.getByLabelText('State'), 'false')
    await waitFor(() => expect(calls.some((c) => c.path === '/api/v1/voice/' && c.query.get('enabled') === 'false')).toBe(true))
  })

  it('plays a PCM voice test by wrapping it as WAV', async () => {
    mockApi({
      ...BASE_ROUTES,
      'POST /api/v1/voice/amy/test': () =>
        audio(new Uint8Array(64), { ...AUDIO_HEADERS, 'X-Opal-Audio-Format': 'pcm' }, 'application/octet-stream'),
    })
    render(<App />)
    await openTab('Voice')
    const row = (await screen.findByText('en_US-amy-low')).closest('tr')!
    await userEvent.click(within(row).getByRole('button', { name: 'Test' }))
    expect(await screen.findByTestId('audio-result')).toHaveTextContent('64 bytes · pcm')
  })
})

describe('Profile', () => {
  it('creates and activates profile', async () => {
    const calls = mockApi({
      ...BASE_ROUTES,
      'POST /api/v1/profile/': () => json({ ...PROFILE, id: 'fast', name: 'fast' }, 201),
      'POST /api/v1/profile/1/activate': { ...PROFILE, is_active: true },
    })
    render(<App />)
    await openTab('Profile')
    const row = (await screen.findByText('default')).closest('tr')!
    await userEvent.click(within(row).getByRole('button', { name: 'Activate' }))
    await waitFor(() => expect(calls.some((c) => c.path === '/api/v1/profile/1/activate')).toBe(true))

    await userEvent.type(screen.getByLabelText('Name'), 'fast')
    await userEvent.selectOptions(screen.getByLabelText('Voice'), 'amy')
    await userEvent.clear(screen.getByLabelText('Speed'))
    await userEvent.type(screen.getByLabelText('Speed'), '1.5')
    await userEvent.click(screen.getByRole('button', { name: 'Create' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.path === '/api/v1/profile/')).toBe(true))
    expect(calls.find((c) => c.method === 'POST' && c.path === '/api/v1/profile/')!.body).toMatchObject({
      name: 'fast',
      modules: { 'opal-say': { voice_id: 'amy', speed: 1.5, volume: 1, output_format: 'wav', sample_rate: null } },
      is_active: false,
    })
  })

  it('surfaces 409 when deleting the active profile', async () => {
    mockApi({
      ...BASE_ROUTES,
      'GET /api/v1/profile/': [{ ...PROFILE, is_active: true }],
      'DELETE /api/v1/profile/1': () => json({ code: 'profile_active', message: 'activate another profile first' }, 409),
    })
    render(<App />)
    await openTab('Profile')
    const row = (await screen.findByText('default')).closest('tr')!
    await userEvent.click(within(row).getByRole('button', { name: 'Delete' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('409 profile_active')
  })
})

describe('Engine', () => {
  it('toggles and validates an engine', async () => {
    const calls = mockApi({
      ...BASE_ROUTES,
      'PATCH /api/v1/engine/piper': { ...ENGINE, enabled: false },
      'POST /api/v1/engine/piper/validate': { valid: true, errors: [], warnings: [{ code: 'engine_disabled', message: 'disabled' }], details: {} },
    })
    render(<App />)
    await openTab('Engine')
    await userEvent.click(await screen.findByRole('button', { name: 'Disable' }))
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ enabled: false }))
    await userEvent.click(screen.getByRole('button', { name: 'Validate' }))
    expect(await screen.findByTestId('validation')).toHaveTextContent('warning: engine_disabled')
  })
})

describe('Setting and backup', () => {
  it('upserts a setting', async () => {
    const calls = mockApi({
      ...BASE_ROUTES,
      'GET /api/v1/setting/': {},
      'PUT /api/v1/setting/chunk_max_chars': { key: 'chunk_max_chars', value: 200, configured_value: 200, default: 400, source: 'persisted', has_override: true, is_default: false, restart_required: false, pending_restart: false },
    })
    render(<App />)
    await openTab('Setting')
    await userEvent.type(screen.getByLabelText('Key'), 'chunk_max_chars')
    await userEvent.type(screen.getByLabelText('Value'), '200')
    await userEvent.click(screen.getByRole('button', { name: 'Set' }))
    expect(await screen.findByTestId('setting-detail')).toHaveTextContent('chunk_max_chars = 200')
    expect(calls.find((c) => c.method === 'PUT')!.body).toEqual({ value: 200 })
  })

  it('saves snapshots and validates live configuration', async () => {
    const calls = mockApi({
      ...BASE_ROUTES,
      'POST /api/v1/config/save': { managed: true, snapshot: { module: 'opal-say', format_version: '1.0.0' } },
      'POST /api/v1/config/validate': {
        valid: false, errors: [{ code: 'voice_not_found', path: 'resources.profiles', message: 'bad ref' }], warnings: [],
      },
    })
    render(<App />)
    await openTab('Configuration')
    await userEvent.click(screen.getByRole('button', { name: 'Save managed snapshot' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Managed snapshot saved.')
    expect(calls.find((call) => call.path === '/api/v1/config/save')?.body).toEqual({ destination: 'managed' })
    await userEvent.click(screen.getByRole('button', { name: 'Validate live configuration' }))
    expect(await screen.findByTestId('snapshot-validation')).toHaveTextContent('voice_not_found')
  })
})

describe('Snapshot clients and authorization', () => {
  it('validates and loads a candidate, then loads the managed snapshot with a strong tag', async () => {
    const calls = mockApi({ ...BASE_ROUTES,
      'POST /api/v1/config/validate': { valid: true, errors: [], warnings: [] },
      'POST /api/v1/config/load': { applied: true, pending_restart_fields: [], warnings: [] },
    })
    const confirmation = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const snapshot = { format_version: '1.0.0', module: 'opal-say', settings: {}, resources: {} }
    render(<App />)
    await openTab('Configuration')
    fireEvent.change(screen.getByLabelText('Candidate snapshot (JSON)'), { target: { value: JSON.stringify(snapshot) } })
    await userEvent.click(screen.getByRole('button', { name: 'Validate candidate' }))
    expect(await screen.findByTestId('snapshot-validation')).toHaveTextContent('true')
    expect(calls.find((call) => call.path === '/api/v1/config/validate')?.body).toEqual({ snapshot })
    await userEvent.click(screen.getByRole('button', { name: 'Load candidate' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Configuration loaded.')
    const load = calls.find((call) => call.path === '/api/v1/config/load')!
    expect(load.body).toEqual({ snapshot })
    expect(load.headers.get('If-Match')).toBe('"r1"')
    await userEvent.click(screen.getByRole('button', { name: 'Load managed snapshot' }))
    await waitFor(() => expect(calls.filter((call) => call.path === '/api/v1/config/load')).toHaveLength(2))
    expect(calls.filter((call) => call.path === '/api/v1/config/load')[1].body).toEqual({})
    expect(confirmation).toHaveBeenCalledTimes(2)
  })

  it('downloads a response snapshot without requesting a managed save', async () => {
    const calls = mockApi({ ...BASE_ROUTES,
      'POST /api/v1/config/save': { managed: false, snapshot: { module: 'opal-say' } },
    })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    URL.revokeObjectURL = vi.fn()
    render(<App />)
    await openTab('Configuration')
    await userEvent.click(screen.getByRole('button', { name: 'Download snapshot' }))
    expect(await screen.findByRole('status')).toHaveTextContent('managed snapshot unchanged')
    expect(calls.find((call) => call.path === '/api/v1/config/save')?.body).toEqual({ destination: 'response' })
    expect(URL.revokeObjectURL).toHaveBeenCalled()
  })

  it('uses an in-memory bearer credential and clears the password field', async () => {
    const calls = mockApi(BASE_ROUTES)
    render(<App />)
    const field = screen.getByLabelText('Bearer credential')
    await userEvent.type(field, 'fixture-token')
    await userEvent.click(screen.getByRole('button', { name: 'Use credential' }))
    await waitFor(() => expect(calls.some((call) => call.headers.get('Authorization') === 'Bearer fixture-token')).toBe(true))
    expect(field).toHaveValue('')
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })
})

describe('audio helpers', () => {
  it('wraps PCM in a valid WAV header', async () => {
    const blob = pcmToWav(new Uint8Array([1, 0, 2, 0]), 16000)
    const buffer = await new Promise<ArrayBuffer>((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(reader.result as ArrayBuffer)
      reader.onerror = () => reject(reader.error)
      reader.readAsArrayBuffer(blob)
    })
    const bytes = new Uint8Array(buffer)
    const view = new DataView(bytes.buffer)
    expect(String.fromCharCode(...bytes.slice(0, 4))).toBe('RIFF')
    expect(view.getUint32(24, true)).toBe(16000)
    expect(view.getUint32(40, true)).toBe(4)
    expect(bytes.byteLength).toBe(48)
  })
})
