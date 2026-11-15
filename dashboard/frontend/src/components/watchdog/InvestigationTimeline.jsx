import React, { useMemo, useState } from 'react'

const KEY_KINDS = new Set([
  'first_deviation', 'first_error', 'alert', 'remediation', 'mitigation', 'recovery',
  'onset', 'inject', 'first deviation',
])

function parseTs(value) {
  const t = Date.parse(value || '')
  return Number.isFinite(t) ? t : null
}

function formatStamp(ts) {
  if (ts == null) return '—'
  try {
    const d = new Date(ts)
    const date = d.toLocaleDateString([], {
      year: 'numeric',
      month: 'short',
      day: '2-digit',
    })
    const time = d.toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    })
    return `${date} ${time}`
  } catch {
    return '—'
  }
}

function formatOffset(seconds) {
  if (seconds == null || !Number.isFinite(seconds)) return '—'
  const sec = Math.round(seconds)
  if (sec === 0) return 'T+0s'
  if (Math.abs(sec) < 60) return `T+${sec}s`
  const m = Math.floor(Math.abs(sec) / 60)
  const s = Math.abs(sec) % 60
  return `T+${m}m${s ? `${s}s` : ''}`
}

function formatScore(score) {
  if (score == null || !Number.isFinite(Number(score))) return null
  return Number(score).toFixed(2)
}

function roleLabel(role) {
  const r = String(role || '').toLowerCase()
  if (!r || r === 'unknown') return null
  if (r === 'victim_of_infra') return 'victim'
  return r
}

function orderingLabel(source) {
  const s = String(source || '').toLowerCase()
  if (!s) return null
  if (s === 'metric_onset' || s === 'anomaly_onset') return 'metric onset'
  if (s === 'trace_dag' || s === 'causal' || s === 'logical_clock') return 'causal'
  if (s === 'wall_clock') return 'wall clock'
  return s.replace(/_/g, ' ')
}

function isCausal(source) {
  const s = String(source || '').toLowerCase()
  return s === 'trace_dag' || s === 'causal' || s === 'logical_clock'
}

function enrichEvents(events, { anomalyByService = {}, roleByService = {} } = {}) {
  const stamped = events.map((ev, idx) => {
    const service = ev.service || ev.node || null
    const ts = parseTs(ev.timestamp)
    const score = ev.anomaly_score != null
      ? Number(ev.anomaly_score)
      : (service && anomalyByService[service] != null ? Number(anomalyByService[service]) : null)
    const role = ev.role || (service ? roleByService[service] : null) || null
    return {
      ...ev,
      service,
      _idx: idx,
      _ts: ts,
      _score: Number.isFinite(score) ? score : null,
      _role: roleLabel(role),
      _source: ev.ordering_source || ev.orderingSource || null,
      _phase: String(ev.event || ev.phase || 'first deviation').replace(/_/g, ' '),
    }
  })

  const times = stamped.map(e => e._ts).filter(t => t != null)
  const origin = times.length ? Math.min(...times) : null

  return stamped
    .map(ev => ({
      ...ev,
      _offsetSec: ev.offset_seconds != null && Number.isFinite(Number(ev.offset_seconds))
        ? Number(ev.offset_seconds)
        : (ev._ts != null && origin != null ? (ev._ts - origin) / 1000 : null),
    }))
    .sort((a, b) => {
      const ta = a._ts ?? Number.POSITIVE_INFINITY
      const tb = b._ts ?? Number.POSITIVE_INFINITY
      if (ta !== tb) return ta - tb
      const sa = a._score ?? -1
      const sb = b._score ?? -1
      if (sa !== sb) return sb - sa
      return String(a.service || '').localeCompare(String(b.service || '')) || a._idx - b._idx
    })
}

function groupSimultaneous(events) {
  const groups = []
  for (const ev of events) {
    const key = ev._ts == null ? `na-${ev._idx}` : String(ev._ts)
    const last = groups[groups.length - 1]
    if (last && last.key === key) last.items.push(ev)
    else groups.push({ key, ts: ev._ts, offsetSec: ev._offsetSec, items: [ev] })
  }
  return groups
}

export default function InvestigationTimeline({
  events = [],
  rootCause = null,
  alertedService = null,
  anomalyScores = null,
  candidates = null,
}) {
  const [keyOnly, setKeyOnly] = useState(true)

  const anomalyByService = useMemo(() => {
    if (!anomalyScores) return {}
    if (Array.isArray(anomalyScores)) {
      return Object.fromEntries(
        anomalyScores.map(row => [row.node || row.service, Number(row.score)]),
      )
    }
    return anomalyScores
  }, [anomalyScores])

  const roleByService = useMemo(() => {
    const out = {}
    for (const c of candidates || []) {
      if (c?.node_id) out[c.node_id] = c.role
    }
    return out
  }, [candidates])

  const enriched = useMemo(
    () => enrichEvents(events, { anomalyByService, roleByService }),
    [events, anomalyByService, roleByService],
  )

  const visible = useMemo(() => {
    if (!keyOnly) return enriched
    const filtered = enriched.filter((ev) => {
      if (ev.key_moment) return true
      if (ev._role === 'origin' || ev._role === 'amplifier') return true
      if (ev.service && (ev.service === rootCause || ev.service === alertedService)) return true
      const kind = String(ev._phase || '').toLowerCase()
      return [...KEY_KINDS].some(k => kind.includes(k))
    })
    return filtered.length ? filtered : enriched.slice(0, 8)
  }, [enriched, keyOnly, rootCause, alertedService])

  const groups = useMemo(() => groupSimultaneous(visible), [visible])
  const simultaneous = groups.some(g => g.items.length > 1)
  const anyCausal = visible.some(ev => isCausal(ev._source))
  const hasScores = visible.some(ev => ev._score != null)

  if (!events.length) {
    return (
      <div className="panel">
        <h2>Timeline</h2>
        <p className="muted">No timeline events.</p>
      </div>
    )
  }

  return (
    <div className="panel inv-timeline">
      <div className="section-heading">
        <div>
          <h2>Timeline</h2>
          <p className="muted">
            {anyCausal
              ? 'Solid rail = causal order · dashed = metric onset'
              : simultaneous
                ? 'Shared onset bucket — same metric sample time, not proven causal order'
                : 'First anomaly onset per service (from metrics)'}
          </p>
        </div>
        <label className="timeline-toggle">
          <input type="checkbox" checked={keyOnly} onChange={e => setKeyOnly(e.target.checked)} />
          Key moments
        </label>
      </div>

      <div className="inv-timeline__meta">
        <span>{visible.length} service{visible.length === 1 ? '' : 's'}</span>
        {simultaneous && <span className="inv-timeline__badge">shared onset time</span>}
        {!hasScores && (
          <span className="muted">Scores unavailable on older reports</span>
        )}
      </div>

      <ol className="inv-timeline__rail">
        {groups.map((group) => {
          const causalGroup = group.items.every(ev => isCausal(ev._source))
          return (
            <li
              key={group.key}
              className={`inv-timeline__group${causalGroup ? ' is-causal' : ' is-clock'}${group.items.length > 1 ? ' is-bundle' : ''}`}
            >
              <div className="inv-timeline__marker" aria-hidden="true" />
              <div className="inv-timeline__when">
                <strong>{formatOffset(group.offsetSec)}</strong>
                <time dateTime={group.ts != null ? new Date(group.ts).toISOString() : undefined}>
                  {formatStamp(group.ts)}
                </time>
                {group.items.length > 1 && (
                  <span className="inv-timeline__bucket">{group.items.length} services</span>
                )}
              </div>
              <div className="inv-timeline__items">
                {group.items.map((ev) => {
                  const isRoot = rootCause && ev.service === rootCause
                  const isAlerted = alertedService && ev.service === alertedService
                  const score = formatScore(ev._score)
                  const source = orderingLabel(ev._source)
                  const iso = ev._ts != null ? new Date(ev._ts).toISOString() : undefined
                  return (
                    <div
                      key={`${ev.service}-${ev._idx}`}
                      className={`inv-timeline__row${isRoot ? ' is-root' : ''}${isAlerted ? ' is-alerted' : ''}`}
                    >
                      <time className="inv-timeline__row-time" dateTime={iso} title={iso || undefined}>
                        <span>{formatOffset(ev._offsetSec)}</span>
                        <span>{formatStamp(ev._ts)}</span>
                      </time>
                      <div className="inv-timeline__service">
                        <strong>{ev.service || '—'}</strong>
                        {isRoot && <span className="inv-timeline__tag inv-timeline__tag--root">origin</span>}
                        {ev._role && ev._role !== 'origin' && (
                          <span className="inv-timeline__tag">{ev._role}</span>
                        )}
                        {isAlerted && !isRoot && <span className="inv-timeline__tag">alerted</span>}
                      </div>
                      <span className="inv-timeline__phase">{ev._phase}</span>
                      {score != null && (
                        <span className="inv-timeline__score" title="anomaly score">
                          a={score}
                        </span>
                      )}
                      {source && (
                        <span className="inv-timeline__source">{source}</span>
                      )}
                    </div>
                  )
                })}
              </div>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
