{{/* The same piece as in the platform chart: "from Traefik", for the NetworkPolicies. */}}
{{- define "relay.fromTraefik" -}}
- namespaceSelector:
    matchLabels: { kubernetes.io/metadata.name: {{ .Values.traefik.namespace }} }
  podSelector:
    matchLabels:
      {{- toYaml .Values.traefik.podLabels | nindent 6 }}
{{- end }}

{{/* "From the gateway": the gateway's pods, in the platform's namespace. */}}
{{- define "relay.fromGateway" -}}
- namespaceSelector:
    matchLabels: { kubernetes.io/metadata.name: {{ .Values.platformNamespace }} }
  podSelector:
    matchLabels: { app: gateway }
{{- end }}