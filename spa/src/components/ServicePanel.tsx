import { useState } from 'react'
import { call } from '../api/client'
import type { ServiceLogTail, ServiceStatus } from '../api/types'
import { ErrorBanner, useAction, useLoad } from './common'

// The managed server is the pidfile-tracked detached process the service launches.
// Start doubles as restart; stopping the managed server ends the process that may
// be serving this UI, so subsequent calls can fail — the status line says why.
export function ServicePanel() {
  const [lines, setLines] = useState(10)
  const [append, setAppend] = useState(false)
  const [status, setStatus] = useState<ServiceStatus | null>(null)
  const log = useLoad(() => call<ServiceLogTail>('opal_service_log', { query: { lines } }), {
    lines: [],
  } as ServiceLogTail)
  const action = useAction()

  const start = () =>
    action.run(async () => {
      setStatus(await call<ServiceStatus>('opal_service_start', { body: { append } }))
    })

  const stop = () =>
    action.run(async () => {
      if (!window.confirm('Stop the managed server process?')) return
      setStatus(await call<ServiceStatus>('opal_service_stop'))
    })

  return (
    <section>
      <h2>Service</h2>
      <p className="muted">
        Manage the detached server process tracked by the pidfile. Output goes to the service log file; start
        replaces a running managed instance. Readiness is reported under Health.
      </p>
      <div className="row">
        <label>
          <input type="checkbox" checked={append} onChange={(e) => setAppend(e.target.checked)} /> append to log
        </label>
        <button onClick={() => void start()} disabled={action.busy}>
          Start
        </button>
        <button onClick={() => void stop()} disabled={action.busy}>
          Stop
        </button>
      </div>
      <ErrorBanner error={action.error} />
      {status && (
        <p className="muted" data-testid="service-status">
          state: <strong className={`status-${status.state}`}>{status.state}</strong>
          {status.pid ? ` · pid ${status.pid}` : ''}
          {status.previous_pid ? ` · replaced pid ${status.previous_pid}` : ''} · log {status.log_file}
        </p>
      )}
      <h3>Log tail</h3>
      <div className="row">
        <label>
          Lines
          <input
            type="number"
            min={1}
            max={1000}
            value={lines}
            onChange={(e) => setLines(Math.max(1, Math.min(1000, Number(e.target.value) || 10)))}
          />
        </label>
        <button onClick={() => void log.reload()}>Refresh</button>
      </div>
      <ErrorBanner error={log.error} />
      <pre className="log-tail" data-testid="service-log">
        {log.data.lines.length ? log.data.lines.join('\n') : '(no log lines)'}
      </pre>
    </section>
  )
}
