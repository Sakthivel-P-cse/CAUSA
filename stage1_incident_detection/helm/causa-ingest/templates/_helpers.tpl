{{/*
Expand the name of the chart.
*/}}
{{- define "causa-ingest.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "causa-ingest.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s" (include "causa-ingest.name" .) | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{- define "causa-ingest.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
app.kubernetes.io/name: {{ include "causa-ingest.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: causa
{{- end }}

{{- define "causa-ingest.selectorLabels" -}}
app.kubernetes.io/name: {{ include "causa-ingest.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
