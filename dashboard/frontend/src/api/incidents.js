export function isAbstainedSummary(summary) {
  return String(summary || '').toLowerCase().startsWith('rca abstained')
}

export function parseAbstainReason(summary) {
  if (!isAbstainedSummary(summary)) return null
  return String(summary).replace(/^RCA abstained:\s*/i, '').trim() || null
}

const ABSTAIN_REASON_LABELS = {
  'MULTI-INCIDENT — ambiguous':
    'Multiple services deviated at once — cannot isolate a single origin.',
  missing_anomaly_baselines:
    'Anomaly baselines are missing — capture baselines before RCA can score origins.',
  'no anomalous candidates':
    'No anomalous services were found in the incident scope.',
}

export function formatAbstainReason(reason) {
  const raw = String(reason || '').trim()
  if (!raw) return 'Insufficient evidence to rank a root cause.'
  return ABSTAIN_REASON_LABELS[raw] || raw
}

export function abstainContext(report = {}) {
  const scope =
    report.incident_scope?.nodes
    || report.blast_radius
    || Object.keys(report.anomaly_scores || {})
  const anomalies = report.anomaly_scores || {}
  const hot = Object.entries(anomalies)
    .filter(([, score]) => Number(score) >= 0.7)
    .sort((a, b) => Number(b[1]) - Number(a[1]))
    .map(([node]) => node)
  return {
    scopeCount: Array.isArray(scope) ? scope.length : 0,
    hotServices: hot,
  }
}

export function formatFailureType(item = {}) {
  if (item.failure_type) return item.failure_type
  if (isAbstainedSummary(item.root_cause_summary)) return 'Abstained'
  const fault = item.remediation?.parsed?.fault_class
  if (fault) return fault
  const alert = String(item.alert_name || '')
  if (/Memory/i.test(alert)) return 'Memory pressure'
  if (/ErrorRate/i.test(alert)) return 'Error rate'
  if (/Latency/i.test(alert)) return 'Latency'
  if (/CPU|Throttle/i.test(alert)) return 'CPU pressure'
  return 'Unclassified'
}

export function normalizeListItem(raw = {}) {
  return {
    id: raw.id || raw.incident_id,
    incident_id: raw.incident_id || raw.id,
    triggered_at: raw.triggered_at,
    alert_name: raw.alert_name,
    alerted_service: raw.alerted_service,
    root_cause_service: raw.root_cause_service,
    failure_type: raw.failure_type,
    severity: String(raw.severity || 'info').toLowerCase(),
    incident_status: raw.incident_status || 'open',
    confidence_score: raw.confidence_score ?? raw.confidence ?? null,
    root_cause_summary: raw.root_cause_summary,
    trace_id: raw.trace_id,
    resolved_at: raw.resolved_at,
    has_rca: Boolean(raw.has_rca),
    window_start: raw.window_start,
    window_end: raw.window_end,
    acked_at: raw.acked_at || null,
    acked_by: raw.acked_by || null,
    lifecycle: raw.lifecycle || (raw.resolved_at ? 'resolved' : raw.acked_at ? 'investigating' : 'active'),
    routing: raw.routing || null,
  }
}

export function normalizeReport(raw = {}) {
  const base = normalizeListItem(raw)
  const anomaly = raw.anomaly_scores || {}
  const anomalyList = (Array.isArray(raw.anomaly_score_list)
    ? raw.anomaly_score_list
    : Object.entries(anomaly).map(([node, score]) => ({ node, score }))
  ).sort((a, b) => Number(b.score) - Number(a.score))

  return {
    ...base,
    ...raw,
    ...normalizeListItem(raw),
    origin_candidates: Array.isArray(raw.origin_candidates) ? raw.origin_candidates : [],
    amplifier_nodes: Array.isArray(raw.amplifier_nodes) ? raw.amplifier_nodes : [],
    blast_radius: Array.isArray(raw.blast_radius) ? raw.blast_radius : [],
    anomaly_scores: typeof anomaly === 'object' && anomaly ? anomaly : {},
    anomaly_score_list: anomalyList,
    evidence: Array.isArray(raw.evidence) ? raw.evidence : [],
    timeline: Array.isArray(raw.timeline) ? raw.timeline : [],
    telemetry_refs: raw.telemetry_refs || {},
    remediation: raw.remediation || null,
    feedback: raw.feedback || null,
    references: raw.references || {},
    inference_evidence: raw.inference_evidence || null,
    incident_scope: raw.incident_scope || null,
  }
}

export function rankedAnomalyScores(report = {}) {
  return [...(report.anomaly_score_list || [])].sort(
    (a, b) => Number(b.score) - Number(a.score),
  )
}

export function sortedTimeline(report = {}) {
  return [...(report.timeline || [])].sort((a, b) => {
    const ta = Date.parse(a.timestamp || '') || 0
    const tb = Date.parse(b.timestamp || '') || 0
    return ta - tb || String(a.service || '').localeCompare(String(b.service || ''))
  })
}

export function buildAbstainEvidence(report = {}) {
  const anomalies = rankedAnomalyScores(report)
  const timeline = sortedTimeline(report)
  const scope = report.blast_radius?.length
    ? report.blast_radius
    : report.incident_scope?.nodes || anomalies.map(row => row.node)
  return { anomalies, timeline, scope }
}

export function normalizeIncidentPage(payload) {
  if (Array.isArray(payload)) {
    return { items: payload.map(normalizeListItem), total: payload.length, facets: {} }
  }
  return {
    items: (payload?.items || []).map(normalizeListItem),
    total: payload?.total ?? 0,
    facets: payload?.facets || {},
  }
}
