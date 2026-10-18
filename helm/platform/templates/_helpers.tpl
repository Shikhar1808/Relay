{{/*
A file starting with "_" renders nothing by itself; it holds pieces other templates include.

"urumi.fromTraefik": the NetworkPolicy "from" entry for the Ingress controller.
Both conditions sit in ONE list item, so both must match: pods with Traefik's labels
AND in Traefik's namespace. Written as two items, either one would do, which is far wider.
*/}}
{{- define "urumi.fromTraefik" -}}
- namespaceSelector:
    matchLabels: { kubernetes.io/metadata.name: {{ .Values.traefik.namespace }} }
  podSelector:
    matchLabels:
      {{- toYaml .Values.traefik.podLabels | nindent 6 }}
{{- end }}