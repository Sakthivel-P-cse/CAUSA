import { formatFailureType, isAbstainedSummary } from './incidents.js'
import { confidenceBand, confidencePct } from './confidence.js'

function topCandidate(report) {
  const candidates = [...(report.origin_candidates || [])].sort(
    (a, b) => Number(b.score || 0) - Number(a.score || 0),
  )
  return candidates[0] || null
}

function rolePhrase(role) {
  const r = String(role || '').toLowerCase()
  if (r === 'origin') return 'the origin'
  if (r === 'amplifier') return 'an amplifier'
  if (r === 'victim' || r === 'victim-of-infra') return 'a downstream victim'
  if (r === 'unknown' || !r) return null
  return role
}

function leadSecondsFromExplanation(explanation) {
  const m = String(explanation || '').match(/deviated\s+(-?\d+(?:\.\d+)?)s\s+before/i)
  if (!m) return null
  const n = Number(m[1])
  return Number.isFinite(n) ? n : null
}

function firstTimelineLead(report, rootNode) {
  const timeline = report.timeline || []
  if (!rootNode || timeline.length < 2) return null
  const rootEvt = timeline.find(e => e.service === rootNode)
  if (!rootEvt?.timestamp) return null
  const rootTs = Date.parse(rootEvt.timestamp)
  if (Number.isNaN(rootTs)) return null
  let earliestOther = null
  for (const e of timeline) {
    if (e.service === rootNode || !e.timestamp) continue
    const t = Date.parse(e.timestamp)
    if (Number.isNaN(t)) continue
    if (earliestOther == null || t < earliestOther) earliestOther = t
  }
  if (earliestOther == null) return null
  return (earliestOther - rootTs) / 1000
}

function signalNums(top) {
  const s = top?.signals || {}
  return {
    T: Number(s.T),
    D: Number(s.D),
    P: Number(s.P),
  }
}

function nearNeutral(value, eps = 0.02) {
  return Number.isFinite(value) && Math.abs(value - 0.5) <= eps
}

function isWeakSignal(top) {
  if (!top) return true
  const role = String(top.role || '').toLowerCase()
  const { T, D, P } = signalNums(top)
  const expl = String(top.explanation || '')
  const slowBurn = role === 'unknown'
    || /slow-burn|no-deviation|T=0\.50 neutral/i.test(expl)
    || nearNeutral(T)
  const noProp = !Number.isFinite(P) || P <= 0.01 || /no outgoing influence/i.test(expl)
  const flatD = !Number.isFinite(D) || nearNeutral(D)
  return slowBurn && noProp && flatD
}

function shortWhy(root, top, alert, failureLabel, roleWords) {
  const { T, D, P } = signalNums(top)
  if (!roleWords || isWeakSignal(top)) {
    const bits = []
    if (!Number.isFinite(T) || nearNeutral(T)) bits.push('no clear onset')
    if (!Number.isFinite(D) || nearNeutral(D)) bits.push('neutral influence')
    if (!Number.isFinite(P) || P <= 0.01) bits.push('no propagation edges')
    return `${root} is the top candidate, but signal is weak (${bits.join(', ') || 'low score'}).`
  }
  const lead =
    leadSecondsFromExplanation(top?.explanation)
    ?? null
  if (lead != null && lead > 0) {
    return `${root} showed anomalous behaviour about ${Math.round(lead)}s before services it influences.`
  }
  if (String(top?.role || '').toLowerCase() === 'origin' && Number.isFinite(P) && P > 0.01) {
    return `${root} ranks as the origin: influence flows outward and timing matches ${alert}.`
  }
  if (String(top?.role || '').toLowerCase() === 'origin') {
    return `${root} ranks as the origin on timing/deviation; no outbound edges for impact.`
  }
  return `${root} is ranked highest (${roleWords}) for this ${failureLabel} incident.`
}

export function plainIncidentSummary(item = {}) {
  const service = item.root_cause_service || item.alerted_service || 'unknown service'
  const name = item.alert_name || 'Alert'
  const raw = String(item.root_cause_summary || item.summary || '')
  if (/slow-burn|no-deviation|out-influence|Role:|RCA abstained/i.test(raw)) {
    if (/abstained/i.test(raw)) return `${name} on ${service} — RCA abstained.`
    return `${name} on ${service} — weak / unknown-role candidate.`
  }
  const m = raw.match(/Top root-cause candidate:\s*([^\s.]+)/i)
  if (m) return `${name} on ${service} — top candidate ${m[1]}.`
  if (raw) {
    const short = raw.length > 120 ? `${raw.slice(0, 119)}…` : raw
    return short
  }
  return `${name} on ${service}`
}

export function buildRcaNarrative(report = {}, { abstained = false } = {}) {
  const top = topCandidate(report)
  const root = report.root_cause_service || top?.node_id || null
  const alert = report.alert_name || 'the alert'
  const alerted = report.alerted_service || null
  const failureLabel = formatFailureType(report)
  const blast = report.blast_radius || []
  const amplifiers = report.amplifier_nodes || []
  const confPct = confidencePct(report.confidence_score ?? top?.confidence)
  const band = confidenceBand(report.confidence_score ?? top?.confidence)
  const weak = isWeakSignal(top)

  if (abstained || isAbstainedSummary(report.root_cause_summary) || !root) {
    const reason = isAbstainedSummary(report.root_cause_summary)
      ? String(report.root_cause_summary).replace(/^RCA abstained:\s*/i, '')
      : 'insufficient signal'
    return {
      headline: `Could not name a root cause for ${alert}`,
      why: `RCA abstained: ${reason}.`,
      impact: 'No impact graph.',
      confidence_label: confPct != null ? `${band} confidence (${confPct}%)` : 'No confidence',
      limited_detail: true,
      limited_reason: 'RCA abstained — not enough evidence to rank origins.',
      root,
      alerted,
      failureLabel,
    }
  }

  const role = top?.role || 'origin'
  const onSvc = alerted ? ` on ${alerted}` : ''
  const roleWords = rolePhrase(role)
  const limited_detail = weak || !roleWords

  let headline
  if (limited_detail) {
    headline = `${root} is the top candidate for ${alert}${onSvc}`
  } else {
    headline = `${root} looks like ${roleWords} of ${alert}${onSvc}`
  }

  const lead = firstTimelineLead(report, root)
  let why = shortWhy(root, top, alert, failureLabel, roleWords)
  if (!limited_detail && lead != null && lead > 0 && !/about \d+s before/.test(why)) {
    why = `${root} showed anomalous behaviour about ${Math.round(lead)}s before services it influences.`
  }

  const parts = []
  if (blast.length) parts.push(`Affected: ${blast.join(', ')}`)
  if (amplifiers.length) parts.push(`Amplifying: ${amplifiers.join(', ')}`)
  const impact = parts.length ? `${parts.join('. ')}.` : 'No blast radius recorded.'

  const confidence_label = confPct != null
    ? `${band} confidence (${confPct}%)${limited_detail ? ' · weak signal' : ''}`
    : 'No confidence'

  return {
    headline,
    why,
    impact,
    confidence_label,
    limited_detail,
    limited_reason: limited_detail ? 'Weak signal — timing/influence too thin to confirm origin.' : null,
    root,
    alerted,
    failureLabel,
  }
}
