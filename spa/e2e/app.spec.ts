import { expect, test, type Page, type Route } from '@playwright/test'

/**
 * The dev-mode client targets http://127.0.0.1:8001; we intercept those calls so
 * the suite needs no live service. Stateful mocks (personas list) model what a
 * real service does — e.g. created entities appear on the next list call.
 */

const API = 'http://127.0.0.1:8001'
const now = '2026-01-01T00:00:00Z'

const engine = { engine_id: 'fake', name: 'Fake Engine', description: null, enabled: true, available: true, options: {}, metadata: {} }
const voice = {
  voice_id: 'v1', name: 'Voice One', description: null, engine_id: 'fake', engine_voice_ref: 'tone-a',
  language: 'en-US', enabled: true, native_sample_rate: 22050, engine_options: {}, metadata: {},
  created_at: now, updated_at: now,
}
const profile = {
  id: 1, name: 'default', description: null, is_active: true, voice_id: 'v1',
  speed: 1, volume: 1, output_format: 'wav', sample_rate: null, engine_options: {},
  created_at: now, updated_at: now,
}
const personas: object[] = [
  { persona_id: 'amy', name: 'Amy', description: 'warm narrator', voice_id: 'v1', enabled: true, created_at: now, updated_at: now },
]

// A minimal valid WAV (44-byte header + a few PCM frames) for completed generate.
function wavBytes(): Buffer {
  const rate = 22050
  const frames = Buffer.alloc(2000)
  const header = Buffer.alloc(44)
  header.write('RIFF', 0); header.writeUInt32LE(36 + frames.length, 4)
  header.write('WAVEfmt ', 8); header.writeUInt32LE(16, 16); header.writeUInt16LE(1, 20)
  header.writeUInt16LE(1, 22); header.writeUInt32LE(rate, 24); header.writeUInt32LE(rate * 2, 28)
  header.writeUInt16LE(2, 32); header.writeUInt16LE(16, 34)
  header.write('data', 36); header.writeUInt32LE(frames.length, 40)
  return Buffer.concat([header, frames])
}

const audioHeaders = {
  'content-type': 'audio/wav',
  'x-opal-audio-format': 'wav',
  'x-opal-sample-rate': '22050',
  'x-opal-voice-id': 'v1',
  'x-opal-engine-id': 'fake',
  'x-opal-profile-id': '1',
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

async function mockApi(page: Page, opts: { failGenerate?: boolean } = {}) {
  await page.route(`${API}/**`, (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname
    const method = route.request().method()

    if (method === 'GET' && path === '/api/v1/info/') return json(route, { name: 'opal-say-svc', version: '1.6.0', api_version: 'v1' })
    if (method === 'GET' && path === '/api/v1/info/ping') return json(route, { status: 'ok' })
    if (method === 'GET' && path === '/api/v1/health/') {
      return json(route, { status: 'ok', version: '1.5.0', active_profile_id: 1, engines: [{ ...engine, message: '' }], details: {} })
    }
    if (method === 'GET' && path === '/api/v1/health/live') return json(route, { status: 'ok', ready: true })
    if (method === 'GET' && path === '/api/v1/health/ready') return json(route, { status: 'ok', ready: true })
    if (method === 'GET' && path === '/api/v1/engines/') return json(route, [engine])
    if (method === 'GET' && path === '/api/v1/voices/') return json(route, [voice])
    if (method === 'GET' && path === '/api/v1/profiles/') return json(route, [profile])
    if (method === 'GET' && path === '/api/v1/settings/') return json(route, { greeting: 'hi' })
    if (method === 'GET' && path === '/api/v1/backup/list') return json(route, [])

    if (path === '/api/v1/persona/') {
      if (method === 'GET') return json(route, personas)
      if (method === 'POST') {
        const body = route.request().postDataJSON()
        personas.push(body)
        return route.fulfill({
          status: 201, contentType: 'application/json',
          headers: { location: `/api/v1/persona/${body.persona_id}` },
          body: JSON.stringify({ ...body, created_at: now, updated_at: now }),
        })
      }
    }
    const personaMatch = path.match(/^\/api\/v1\/personas\/([^/]+)$/)
    if (personaMatch) {
      const found = personas.find((p: any) => p.persona_id === personaMatch[1]) as any
      if (method === 'GET') return found ? json(route, found) : json(route, { error: { code: 'not_found' } }, 404)
      if (method === 'PATCH') return json(route, { ...found, ...route.request().postDataJSON(), updated_at: now })
      if (method === 'DELETE') {
        personas.splice(personas.indexOf(found), 1)
        return route.fulfill({ status: 204 })
      }
    }

    if (method === 'POST' && path === '/api/v1/generate') {
      if (opts.failGenerate) {
        return json(route, { error: { code: 'engine_unavailable', message: 'piper-tts runtime is not installed' } }, 503)
      }
      return route.fulfill({ status: 200, headers: audioHeaders, body: wavBytes() })
    }
    if (method === 'POST' && path === '/api/v1/generate/stream') {
      return route.fulfill({
        status: 200,
        headers: { ...audioHeaders, 'content-type': 'application/octet-stream', 'x-opal-audio-format': 'pcm' },
        body: Buffer.alloc(4096),
      })
    }

    return json(route, { error: { code: 'not_found', message: `unmocked ${method} ${path}` } }, 404)
  })
}

test.beforeEach(async ({ page }) => {
  await mockApi(page)
  await page.goto('/ui/')
})

test('navigates every tab', async ({ page }) => {
  const tabs: Record<string, RegExp> = {
    Generate: /^Generate$/,
    Voices: /^Voices$/,
    Profiles: /^Profiles$/,
    Personas: /^Personas$/,
    Engines: /^Engines$/,
    Settings: /^Settings$/,
    Backup: /^Backup/,
    Health: /^Health$/,
  }
  for (const [tab, heading] of Object.entries(tabs)) {
    await page.getByRole('button', { name: tab, exact: true }).click()
    await expect(page.getByRole('heading', { name: heading }).first()).toBeVisible()
  }
})

test('renders persona and creates one', async ({ page }) => {
  await page.getByRole('button', { name: 'Personas', exact: true }).click()
  // First data fetch can be slow while Vite transforms modules on a cold server.
  await expect(page.getByRole('cell', { name: 'amy', exact: true })).toBeVisible({ timeout: 15_000 })

  await page.getByLabel('Persona ID').fill('hal')
  await page.getByLabel('Name').fill('HAL')
  await page.getByLabel('Voice').selectOption('v1')
  await page.getByLabel('Description').fill('calm computer')
  await page.getByRole('button', { name: 'Create' }).click()

  await expect(page.getByRole('cell', { name: 'hal', exact: true })).toBeVisible()
})

test('generates completed audio and shows the player', async ({ page }) => {
  await page.getByRole('button', { name: 'Generate', exact: true }).click()
  await page.getByLabel('Text').fill('Hello from the e2e suite.')
  await page.getByLabel('Persona').selectOption('amy')
  await page.getByRole('button', { name: 'Generate audio' }).click()
  await expect(page.getByTestId('audio-result')).toBeVisible()
})

test('shows an error banner when generation fails', async ({ page }) => {
  await mockApi(page, { failGenerate: true })
  await page.getByLabel('Text').fill('this will fail')
  await page.getByRole('button', { name: 'Generate audio' }).click()
  await expect(page.getByRole('alert')).toContainText('engine_unavailable')
})
