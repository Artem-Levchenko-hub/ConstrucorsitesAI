{{/*
Naming, labels and the image/secret contracts.

Every component (app, max-core, postgres) gets its own name, its own selector
labels and its own Service, so a selector can never accidentally span two
workloads.
*/}}

{{- define "max-app.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "max-app.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "max-app.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "max-app.app.fullname" -}}
{{- printf "%s-app" (include "max-app.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "max-app.core.fullname" -}}
{{- printf "%s-core" (include "max-app.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "max-app.postgres.fullname" -}}
{{- printf "%s-postgres" (include "max-app.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Labels shared by every object. */}}
{{- define "max-app.labels" -}}
helm.sh/chart: {{ include "max-app.chart" . }}
app.kubernetes.io/name: {{ include "max-app.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/part-of: max-app
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- with .Values.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{/*
Selector labels for one component.
Call with: (dict "context" $ "component" "app")
These are immutable for the life of a Deployment/StatefulSet, so they carry
only identity — never a version or a chart revision.
*/}}
{{- define "max-app.selectorLabels" -}}
app.kubernetes.io/name: {{ include "max-app.name" .context }}
app.kubernetes.io/instance: {{ .context.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/* Full label set for one component. */}}
{{- define "max-app.componentLabels" -}}
{{ include "max-app.labels" .context }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "max-app.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "max-app.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/*
Image reference.
Call with: (dict "image" .Values.app.image "component" "app")

Refuses an empty repository, an empty tag+digest pair, a malformed digest and
the mutable "latest" tag. A digest always wins over a tag.
*/}}
{{- define "max-app.image" -}}
{{- $image := .image -}}
{{- $component := .component -}}
{{- if not $image.repository -}}
{{- fail (printf "max-app: %s image repository is empty; set %s.image.repository" $component $component) -}}
{{- end -}}
{{- if $image.digest -}}
{{- if not (regexMatch "^sha256:[0-9a-f]{64}$" $image.digest) -}}
{{- fail (printf "max-app: %s image digest %q must look like sha256:<64 lowercase hex>" $component $image.digest) -}}
{{- end -}}
{{- printf "%s@%s" $image.repository $image.digest -}}
{{- else -}}
{{- if not $image.tag -}}
{{- fail (printf "max-app: %s image needs a digest (preferred) or a tag" $component) -}}
{{- end -}}
{{- if eq $image.tag "latest" -}}
{{- fail (printf "max-app: %s image tag \"latest\" is mutable; pin %s.image.digest or an immutable tag" $component $component) -}}
{{- end -}}
{{- printf "%s:%s" $image.repository $image.tag -}}
{{- end -}}
{{- end -}}

{{/* Name of the Secret every credential is read from. */}}
{{- define "max-app.secretName" -}}
{{- if .Values.secrets.existingSecret -}}
{{- .Values.secrets.existingSecret -}}
{{- else -}}
{{- include "max-app.fullname" . -}}
{{- end -}}
{{- end -}}

{{/*
One Secret-backed env var.
Call with: (dict "context" $ "name" "DATABASE_URL" "key" "database-url" "optional" true)
*/}}
{{- define "max-app.secretEnv" -}}
- name: {{ .name }}
  valueFrom:
    secretKeyRef:
      name: {{ include "max-app.secretName" .context }}
      key: {{ .key }}
      {{- if .optional }}
      optional: true
      {{- end }}
{{- end -}}

{{/* Public origin the trusted core enforces; derived from the ingress host. */}}
{{- define "max-app.publicOrigin" -}}
{{- if .Values.publicOrigin -}}
{{- .Values.publicOrigin -}}
{{- else if .Values.ingress.host -}}
{{- printf "https://%s" .Values.ingress.host -}}
{{- end -}}
{{- end -}}

{{/* PostgreSQL DSN. Only ever rendered inside a Secret. */}}
{{- define "max-app.postgresDsn" -}}
{{- $database := .database -}}
{{- $root := .context -}}
{{- $postgres := $root.Values.postgres -}}
{{- printf "postgresql://%s:%s@%s:%d/%s" $postgres.username (urlquery .password) (include "max-app.postgres.fullname" $root) (int $postgres.service.port) $database -}}
{{- end -}}

{{/*
Pod-level security context shared by every workload:
non-root, no privilege escalation, RuntimeDefault seccomp.
Call with: (dict "context" $ "component" .Values.app)
*/}}
{{- define "max-app.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: {{ .component.runAsUser }}
runAsGroup: {{ .component.runAsGroup }}
fsGroup: {{ .component.fsGroup | default .component.runAsGroup }}
fsGroupChangePolicy: OnRootMismatch
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{/* Container-level security context. */}}
{{- define "max-app.containerSecurityContext" -}}
allowPrivilegeEscalation: false
privileged: false
readOnlyRootFilesystem: {{ .component.readOnlyRootFilesystem }}
runAsNonRoot: true
runAsUser: {{ .component.runAsUser }}
runAsGroup: {{ .component.runAsGroup }}
capabilities:
  drop:
    - ALL
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{/*
Writable emptyDir volumes are written inline in each workload template rather
than through a helper: the indentation then stays visible where it matters.
*/}}
