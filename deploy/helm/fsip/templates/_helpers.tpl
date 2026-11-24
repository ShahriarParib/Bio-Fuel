{{/* Image reference: local "fsip/<svc>:<tag>" in kind, or "<registry>/fsip-<svc>:<tag>" for a real registry. */}}
{{- define "fsip.image" -}}
{{- $reg := .root.Values.image.registry -}}
{{- if $reg -}}{{ $reg }}/fsip-{{ .svc }}:{{ .root.Values.image.tag }}
{{- else -}}fsip/{{ .svc }}:{{ .root.Values.image.tag }}{{- end -}}
{{- end -}}

{{- define "fsip.labels" -}}
app.kubernetes.io/name: fsip
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Values.image.tag | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{/*
Env shared by every one of our services: where the other services live.
POSTGRES_PASSWORD comes from the Secret through envFrom; the kubelet resolves envFrom values before it
expands $(VAR) in env, so DATABASE_URL below is complete without repeating the password anywhere.
*/}}
{{- define "fsip.env" -}}
- name: SIM_BASE_URL
  value: http://{{ .Release.Name }}-simulator:8000
- name: INTELLIGENCE_URL
  value: http://{{ .Release.Name }}-intelligence:8200
- name: ENGINE_INTERNAL_URL
  value: http://{{ .Release.Name }}-engine:8100
- name: REDIS_URL
  value: redis://{{ .Release.Name }}-redis:6379/0
- name: DATABASE_URL
  value: postgresql+asyncpg://fsip:$(POSTGRES_PASSWORD)@{{ .Release.Name }}-postgres:5432/fsip
- name: APP_VERSION
  value: {{ .Values.image.tag | quote }}
{{- end -}}

{{- define "fsip.envfrom" -}}
- configMapRef:
    name: {{ .Release.Name }}-config
- secretRef:
    name: {{ .Release.Name }}-secrets
{{- end -}}

{{/*
The console's nginx config. Kept as a named template so the Deployment can checksum it without a file
including itself (which Helm would follow as infinite recursion).
*/}}
{{- define "fsip.nginxconf" -}}
server {
    listen 80;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;

    # Re-resolve the api Service on every request window, so pods the autoscaler adds receive traffic
    # without an nginx reload. nginx needs a resolver ADDRESS here, not a name: this is the cluster DNS IP.
    resolver {{ .Values.frontend.dnsResolver }} valid=5s ipv6=off;
    set $api_upstream http://{{ .Release.Name }}-api.{{ .Release.Namespace }}.svc.cluster.local:8080;
    set $bg_upstream  http://{{ .Release.Name }}-breakglass.{{ .Release.Namespace }}.svc.cluster.local:80;

    # Server-Sent Events: no buffering, long timeouts
    location = /api/stream {
        proxy_pass $api_upstream;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 1h;
        add_header X-Accel-Buffering no;
    }

    location /api/ {
        proxy_pass $api_upstream;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_connect_timeout 2s;
        proxy_read_timeout 30s;
        client_max_body_size 256k;
    }

    # Break-glass static plan. Served by a sidecar in the engine pod, so it stays readable when the
    # engine process itself is dead or crash-looping.
    location /breakglass/ {
        proxy_pass $bg_upstream/breakglass/;
        proxy_connect_timeout 2s;
        proxy_read_timeout 10s;
    }

    location / {
        try_files $uri /index.html;
    }

    gzip on;
    gzip_types text/css application/javascript application/json image/svg+xml;
}
{{- end -}}
