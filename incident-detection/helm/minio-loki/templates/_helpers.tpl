{{- define "minio-loki.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "minio-loki.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- printf "%s" $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{- define "minio-loki.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{ include "minio-loki.selectorLabels" . }}
app.kubernetes.io/version: {{ .Values.image.tag | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: causa
{{- end }}

{{- define "minio-loki.selectorLabels" -}}
app.kubernetes.io/name: {{ include "minio-loki.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "minio-loki.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "minio-loki.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Name of the Secret holding the MinIO root credentials.
- prod: a pre-created existingSecret
- dev:  the Secret this chart renders
*/}}
{{- define "minio-loki.secretName" -}}
{{- if .Values.auth.existingSecret -}}
{{ .Values.auth.existingSecret }}
{{- else -}}
{{ include "minio-loki.fullname" . }}-secret
{{- end }}
{{- end }}

{{/*
In-cluster S3 endpoint (host:port) that Loki connects to.
*/}}
{{- define "minio-loki.endpoint" -}}
{{ include "minio-loki.fullname" . }}.{{ .Release.Namespace }}.svc.cluster.local:{{ .Values.service.ports.api }}
{{- end }}
