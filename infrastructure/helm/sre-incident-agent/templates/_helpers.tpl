{{/*
Expand the name of the chart.
*/}}
{{- define "sre-incident-agent.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name. Truncated at 63 chars because
some Kubernetes name fields are limited to that length by the DNS naming
spec (RFC 1123).
*/}}
{{- define "sre-incident-agent.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "sre-incident-agent.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "sre-incident-agent.labels" -}}
helm.sh/chart: {{ include "sre-incident-agent.chart" . }}
{{ include "sre-incident-agent.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "sre-incident-agent.selectorLabels" -}}
app.kubernetes.io/name: {{ include "sre-incident-agent.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Name of the ServiceAccount to use.
*/}}
{{- define "sre-incident-agent.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "sre-incident-agent.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Name of the Secret containing sensitive env vars (POSTGRES_DSN,
ANTHROPIC_API_KEY, SLACK_WEBHOOK_URL, etc.), synced out-of-band by External
Secrets Operator / the AWS Secrets Manager CSI driver from the entries
created in infrastructure/terraform/modules/secrets.
*/}}
{{- define "sre-incident-agent.secretName" -}}
{{- default (include "sre-incident-agent.fullname" .) .Values.secretName }}
{{- end }}
