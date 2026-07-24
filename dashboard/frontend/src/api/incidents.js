export function isAbstainedSummary(summary) {
  return String(summary || '').toLowerCase().startsWith('rca abstained')
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
  }
}

export function normalizeReport(raw = {}) {
  const base = normalizeListItem(raw)
  const anomaly = raw.anomaly_scores || {}
  const anomalyList = Array.isArray(raw.anomaly_score_list)
    ? raw.anomaly_score_list
    : Object.entries(anomaly).map(([node, score]) => ({ node, score }))

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
  }
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
