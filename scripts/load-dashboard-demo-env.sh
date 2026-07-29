#!/usr/bin/env bash
# Load existing local-demo dashboard credentials without printing them.
# Explicitly supplied environment variables take precedence.

load_dashboard_demo_env() {
  local context="${CONTEXT:-kind-gcp-demo-test}"
  local namespace="${CAUSA_DASHBOARD_NAMESPACE:-causa-dashboard}"
  local secret_name="${CAUSA_DASHBOARD_SECRET:-causa-dashboard-auth}"
  local secret_json

  secret_json="$(kubectl --context "${context}" -n "${namespace}" get secret "${secret_name}" -o json 2>/dev/null)" || return 0

  set_from_secret_if_empty() {
    local variable="$1"
    local key="$2"
    local current="${!variable:-}"
    local encoded
    [[ -z "${current}" ]] || return 0
    encoded="$(jq -r --arg key "${key}" '.data[$key] // empty' <<<"${secret_json}")"
    [[ -n "${encoded}" ]] || return 0
    printf -v "${variable}" '%s' "$(printf '%s' "${encoded}" | base64 -d)"
    export "${variable}"
  }

  set_from_secret_if_empty PGPASSWORD PGPASSWORD
  set_from_secret_if_empty REMEDIATION_PGPASSWORD REMEDIATION_PGPASSWORD
}
