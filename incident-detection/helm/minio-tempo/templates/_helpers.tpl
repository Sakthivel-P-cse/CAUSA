{{- define "minio-tempo.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "minio-tempo.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- printf "%s" $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{- define "minio-tempo.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{ include "minio-tempo.selectorLabels" . }}
app.kubernetes.io/version: {{ .Values.image.tag | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: causa
{{- end }}

{{- define "minio-tempo.selectorLabels" -}}
app.kubernetes.io/name: {{ include "minio-tempo.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "minio-tempo.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "minio-tempo.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Name of the Secret holding the MinIO root credentials.
- prod: a pre-created existingSecret
- dev:  the Secret this chart renders
*/}}
{{- define "minio-tempo.secretName" -}}
{{- if .Values.auth.existingSecret -}}
{{ .Values.auth.existingSecret }}
{{- else -}}
{{ include "minio-tempo.fullname" . }}-secret
{{- end }}
{{- end }}

{{/*
In-cluster S3 endpoint (host:port) that Tempo connects to.
*/}}
{{- define "minio-tempo.endpoint" -}}
{{ include "minio-tempo.fullname" . }}.{{ .Release.Namespace }}.svc.cluster.local:{{ .Values.service.ports.api }}
{{- end }}
