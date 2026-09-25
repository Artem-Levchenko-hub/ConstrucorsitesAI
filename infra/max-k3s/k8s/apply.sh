#!/usr/bin/env bash
# Уровень Kubernetes (запускать с Mac; туннели поднимаются сами, kubeconfig ~/.kube/max-studio.yaml):
#   ./apply.sh coredns          # <домен> внутри кластеров резолвится с авторитетных NS reg.ru (все три)
#   ./apply.sh cert-manager     # cert-manager + ClusterIssuer'ы Let's Encrypt (все три кластера)
#   ./apply.sh registry         # приватный Docker registry на runtime: https://registry.<домен>
#   ./apply.sh registries-yaml  # креды registry во все K3s (/etc/rancher/k3s/registries.yaml)
#   ./apply.sh monitoring       # kube-prometheus-stack на core: https://grafana.<домен>, node-exporter всех VPS
#   ./apply.sh placeholder      # заглушка с сертификатом на https://<домен> и www до переезда платформы
#   ./apply.sh orchestrator-access  # ServiceAccount оркестратора в runtime + kubeconfig на core (Фаза 3, этап A)
#   ./apply.sh publication-env  # .env оркестратора на core: реестр, суффиксы; PUBLICATION_BACKEND НЕ включает
#   ./apply.sh all
# Секреты (пароли registry/grafana) живут только на хостах: /etc/max-studio/*.env (root, 0600).
set -euo pipefail
. "$(dirname "$0")/../lib.sh"
export KUBECONFIG="$HOME/.kube/max-studio.yaml"
"$HERE/kube-tunnel.sh" up >/dev/null

CERT_MANAGER_VERSION=v1.21.2          # 2026-09-22
KUBE_PROM_STACK_VERSION=91.4.1        # 2026-09-22
REGISTRY_USER=max

# host_secret <host> <file> <key> — прочитать/создать секрет на хосте (идемпотентно), вернуть значение
host_secret() {
  local h=$1 file=$2 key=$3 v
  v=$(sh_remote "$h" "sed -n 's/^$key=//p' /etc/max-studio/$file 2>/dev/null" || true)
  if [ -z "$v" ]; then
    v=$(openssl rand -base64 30 | tr -d '/+=' | cut -c1-32)
    sh_remote "$h" "install -d -m 711 /etc/max-studio; touch /etc/max-studio/$file; chmod 600 /etc/max-studio/$file; sed -i '/^$key=/d' /etc/max-studio/$file; echo '$key=$v' >> /etc/max-studio/$file"
  fi
  echo "$v"
}

phase_cert_manager() {
  helm repo add jetstack https://charts.jetstack.io --force-update >/dev/null
  for h in $HOSTS; do
    log "cert-manager → max-$h"
    helm upgrade --install cert-manager jetstack/cert-manager --kube-context "max-$h" \
      --namespace cert-manager --create-namespace --version "$CERT_MANAGER_VERSION" \
      --set crds.enabled=true --set prometheus.enabled=true --wait --timeout 5m >/dev/null
    kubectl --context "max-$h" apply -f - <<EOF
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata: {name: letsencrypt-prod}
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    privateKeySecretRef: {name: letsencrypt-prod-account}
    solvers:
      - http01: {ingress: {ingressClassName: traefik}}
---
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata: {name: letsencrypt-staging}
spec:
  acme:
    server: https://acme-staging-v02.api.letsencrypt.org/directory
    privateKeySecretRef: {name: letsencrypt-staging-account}
    solvers:
      - http01: {ingress: {ingressClassName: traefik}}
EOF
    kubectl --context "max-$h" get clusterissuer --no-headers | sed 's/^/  /'
  done
}

phase_registry() {
  log "registry → max-runtime (https://registry.$DOMAIN)"
  local pw http_secret ht
  pw=$(host_secret runtime registry.env REGISTRY_PASSWORD)
  sh_remote runtime "grep -q '^REGISTRY_USER=' /etc/max-studio/registry.env || echo 'REGISTRY_USER=$REGISTRY_USER' >> /etc/max-studio/registry.env; grep -q '^REGISTRY_HOST=' /etc/max-studio/registry.env || echo 'REGISTRY_HOST=registry.$DOMAIN' >> /etc/max-studio/registry.env"
  http_secret=$(host_secret runtime registry.env REGISTRY_HTTP_SECRET)
  ht=$(htpasswd -nbB "$REGISTRY_USER" "$pw")
  kubectl --context max-runtime apply -f - <<EOF
apiVersion: v1
kind: Namespace
metadata: {name: registry}
---
apiVersion: v1
kind: Secret
metadata: {name: registry-auth, namespace: registry}
type: Opaque
stringData: {htpasswd: "$ht"}
---
apiVersion: v1
kind: Secret
metadata: {name: registry-http, namespace: registry}
type: Opaque
stringData: {secret: "$http_secret"}
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata: {name: registry-data, namespace: registry}
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: local-path
  resources: {requests: {storage: 250Gi}}
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: registry, namespace: registry}
spec:
  replicas: 1
  strategy: {type: Recreate}
  selector: {matchLabels: {app: registry}}
  template:
    metadata: {labels: {app: registry}}
    spec:
      securityContext: {fsGroup: 1000}
      containers:
        - name: registry
          image: registry:2
          ports: [{containerPort: 5000, name: http}]
          env:
            - {name: REGISTRY_AUTH, value: htpasswd}
            - {name: REGISTRY_AUTH_HTPASSWD_REALM, value: "MAX Studio Registry"}
            - {name: REGISTRY_AUTH_HTPASSWD_PATH, value: /auth/htpasswd}
            - {name: REGISTRY_STORAGE_DELETE_ENABLED, value: "true"}
            - {name: REGISTRY_HTTP_RELATIVEURLS, value: "true"}
            - {name: REGISTRY_HTTP_SECRET, valueFrom: {secretKeyRef: {name: registry-http, key: secret}}}
          volumeMounts:
            - {name: data, mountPath: /var/lib/registry}
            - {name: auth, mountPath: /auth, readOnly: true}
          readinessProbe: {httpGet: {path: /, port: http}, initialDelaySeconds: 3, periodSeconds: 10}
          livenessProbe: {httpGet: {path: /, port: http}, initialDelaySeconds: 10, periodSeconds: 30}
          resources: {requests: {cpu: 100m, memory: 128Mi}, limits: {memory: 2Gi}}
      volumes:
        - {name: data, persistentVolumeClaim: {claimName: registry-data}}
        - {name: auth, secret: {secretName: registry-auth}}
---
apiVersion: v1
kind: Service
metadata: {name: registry, namespace: registry}
spec:
  selector: {app: registry}
  ports: [{port: 5000, targetPort: http, name: http}]
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: registry
  namespace: registry
  annotations: {cert-manager.io/cluster-issuer: letsencrypt-prod}
spec:
  ingressClassName: traefik
  tls: [{hosts: [registry.$DOMAIN], secretName: registry-tls}]
  rules:
    - host: registry.$DOMAIN
      http:
        paths:
          - {path: /, pathType: Prefix, backend: {service: {name: registry, port: {name: http}}}}
EOF
  kubectl --context max-runtime -n registry rollout status deploy/registry --timeout=3m | sed 's/^/  /'
}

phase_registries_yaml() {
  log "registries.yaml → все K3s (pull из registry.$DOMAIN с кредами)"
  local pw; pw=$(host_secret runtime registry.env REGISTRY_PASSWORD)
  for h in $HOSTS; do
    sh_remote "$h" "cat > /etc/rancher/k3s/registries.yaml <<EOF
# generated by infra/max-k3s/k8s/apply.sh registries-yaml
configs:
  registry.$DOMAIN:
    auth:
      username: $REGISTRY_USER
      password: $pw
EOF
chmod 600 /etc/rancher/k3s/registries.yaml; systemctl restart k3s"
    echo "  $h: registries.yaml обновлён, k3s перезапущен"
  done
}

phase_monitoring() {
  log "monitoring → max-core (kube-prometheus-stack $KUBE_PROM_STACK_VERSION, https://grafana.$DOMAIN)"
  helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update >/dev/null
  local gpw; gpw=$(host_secret core grafana.env GRAFANA_ADMIN_PASSWORD)
  sh_remote core "grep -q '^GRAFANA_URL=' /etc/max-studio/grafana.env || echo 'GRAFANA_URL=https://grafana.$DOMAIN' >> /etc/max-studio/grafana.env; grep -q '^GRAFANA_ADMIN_USER=' /etc/max-studio/grafana.env || echo 'GRAFANA_ADMIN_USER=admin' >> /etc/max-studio/grafana.env"
  local targets=""; for h in $HOSTS; do targets="$targets
          - targets: ['$(inv "$h" wg):9100']
            labels: {vps: $h, cluster: $h}"; done
  helm upgrade --install monitoring prometheus-community/kube-prometheus-stack --kube-context max-core \
    --namespace monitoring --create-namespace --version "$KUBE_PROM_STACK_VERSION" --wait --timeout 10m \
    -f - <<EOF >/dev/null
fullnameOverride: monitoring
grafana:
  adminPassword: "$gpw"
  # На core порты 80/443 держит хостовый nginx (Фаза 2a: платформа в Docker), Traefik — только ClusterIP.
  # TLS для grafana.$DOMAIN выпускает certbot в nginx, ingress здесь — HTTP без cert-manager.
  ingress:
    enabled: true
    ingressClassName: traefik
    hosts: [grafana.$DOMAIN]
  grafana.ini:
    server: {root_url: "https://grafana.$DOMAIN"}
    analytics: {reporting_enabled: false, check_for_updates: false}
  persistence: {enabled: true, storageClassName: local-path, size: 5Gi}
  resources: {requests: {cpu: 50m, memory: 128Mi}, limits: {memory: 512Mi}}
prometheus:
  prometheusSpec:
    retention: 15d
    retentionSize: 40GB
    storageSpec:
      volumeClaimTemplate:
        spec: {storageClassName: local-path, accessModes: [ReadWriteOnce], resources: {requests: {storage: 50Gi}}}
    resources: {requests: {cpu: 200m, memory: 1Gi}, limits: {memory: 4Gi}}
    # node-exporter всех трёх VPS слушает на WG-адресах (см. remote/10-wireguard.sh)
    additionalScrapeConfigs:
      - job_name: node-exporter
        static_configs:$targets
alertmanager:
  alertmanagerSpec:
    storage:
      volumeClaimTemplate:
        spec: {storageClassName: local-path, accessModes: [ReadWriteOnce], resources: {requests: {storage: 2Gi}}}
nodeExporter: {enabled: false}
# k3s — единый процесс: отдельных endpoint'ов scheduler/controller-manager/etcd/kube-proxy нет
kubeControllerManager: {enabled: false}
kubeScheduler: {enabled: false}
kubeEtcd: {enabled: false}
kubeProxy: {enabled: false}
EOF
  kubectl --context max-core -n monitoring get pods --no-headers | sed 's/^/  /'
}

phase_coredns() {
  # Наш домен внутри кластеров резолвится напрямую с авторитетных NS reg.ru: провайдерские
  # резолверы (Yandex DNS) кэшируют «нет такого имени» до 3 часов, и cert-manager после смены
  # DNS столько же не может пройти self-check HTTP-01. k3s подхватывает ConfigMap coredns-custom.
  # у forward-плагина CoreDNS лимит 15 upstream'ов, у reg.ru их 16 — берём по два от каждого NS
  local ns_ips; ns_ips=$( (dig +short A ns1.reg.ru | sort | head -2; dig +short A ns2.reg.ru | sort | head -2) | tr '\n' ' ')
  [ -n "$ns_ips" ] || { echo "coredns: не смог узнать IP ns1/ns2.reg.ru" >&2; return 1; }
  for h in $HOSTS; do
    log "coredns → max-$h: $DOMAIN → авторитетные NS reg.ru ($ns_ips)"
    kubectl --context "max-$h" apply -f - <<EOF
apiVersion: v1
kind: ConfigMap
metadata: {name: coredns-custom, namespace: kube-system}
data:
  $DOMAIN.server: |
    $DOMAIN:53 {
        errors
        cache 30
        forward . $ns_ips
    }
EOF
    kubectl --context "max-$h" -n kube-system rollout restart deploy/coredns >/dev/null
    kubectl --context "max-$h" -n kube-system rollout status deploy/coredns --timeout=2m | tail -1 | sed 's/^/  /'
  done
}

phase_placeholder() {
  # Заглушка на корне домена (yleum.ru + www) с настоящим сертификатом — чтобы до переезда
  # платформы браузер не показывал предупреждение. При переезде: kubectl delete ns landing.
  log "placeholder → max-core (https://$DOMAIN, https://www.$DOMAIN)"
  kubectl --context max-core apply -f - <<EOF
apiVersion: v1
kind: Namespace
metadata: {name: landing}
---
apiVersion: v1
kind: ConfigMap
metadata: {name: landing-html, namespace: landing}
data:
  index.html: |
    <!doctype html><html lang="ru"><head><meta charset="utf-8"><title>MAX Studio</title>
    <meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
    <style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b0d12;color:#e8eaf0;font:16px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
    main{text-align:center;padding:2rem}h1{font-size:2.2rem;margin:0 0 .5rem;letter-spacing:.02em}p{margin:0;color:#9aa3b5}</style></head>
    <body><main><h1>MAX Studio</h1><p>Платформа переезжает на новую инфраструктуру. Скоро.</p></main></body></html>
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: landing, namespace: landing}
spec:
  replicas: 1
  selector: {matchLabels: {app: landing}}
  template:
    metadata: {labels: {app: landing}}
    spec:
      containers:
        - name: nginx
          image: nginx:1.27-alpine
          ports: [{containerPort: 80, name: http}]
          volumeMounts: [{name: html, mountPath: /usr/share/nginx/html, readOnly: true}]
          resources: {requests: {cpu: 10m, memory: 16Mi}, limits: {memory: 64Mi}}
      volumes: [{name: html, configMap: {name: landing-html}}]
---
apiVersion: v1
kind: Service
metadata: {name: landing, namespace: landing}
spec: {selector: {app: landing}, ports: [{port: 80, targetPort: http}]}
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: landing
  namespace: landing
  annotations: {cert-manager.io/cluster-issuer: letsencrypt-prod}
spec:
  ingressClassName: traefik
  tls: [{hosts: [$DOMAIN, www.$DOMAIN], secretName: landing-tls}]
  rules:
    - host: $DOMAIN
      http: {paths: [{path: /, pathType: Prefix, backend: {service: {name: landing, port: {number: 80}}}}]}
    - host: www.$DOMAIN
      http: {paths: [{path: /, pathType: Prefix, backend: {service: {name: landing, port: {number: 80}}}}]}
EOF
  kubectl --context max-core -n landing rollout status deploy/landing --timeout=2m | tail -1 | sed 's/^/  /'
}

# Фаза 3 / этап A: оркестратор на core размещает опубликованные приложения в кластере runtime.
# Доступ — отдельный ServiceAccount с ролью ровно под объекты одного приложения (namespace создать
# и пометить можно, удалить — нельзя; секреты/деплойменты/PVC/ingress/networkpolicy — в любом namespace).
# Kubeconfig с долгоживущим токеном кладётся на core в /etc/max-studio/runtime-kubeconfig.yaml,
# читать его может только пользователь оркестратора ($ADMIN_USER); API — по WireGuard (10.10.0.2:6443).
phase_orchestrator_access() {
  log "orchestrator-access → ServiceAccount omnia-orchestrator в runtime, kubeconfig → core"
  kubectl --context max-runtime apply -f - <<'YAML'
apiVersion: v1
kind: Namespace
metadata: {name: max-system}
---
apiVersion: v1
kind: ServiceAccount
metadata: {name: omnia-orchestrator, namespace: max-system}
---
apiVersion: v1
kind: Secret
metadata:
  name: omnia-orchestrator-token
  namespace: max-system
  annotations: {kubernetes.io/service-account.name: omnia-orchestrator}
type: kubernetes.io/service-account-token
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata: {name: omnia-orchestrator-publisher}
rules:
  - apiGroups: [""]
    resources: [namespaces]
    verbs: [get, list, watch, create, patch]
  - apiGroups: [""]
    resources: [secrets, configmaps, services, persistentvolumeclaims]
    verbs: [get, list, watch, create, patch, delete]
  - apiGroups: [""]
    resources: [pods]
    verbs: [get, list, watch]
  - apiGroups: [""]
    resources: [pods/exec, pods/log]
    verbs: [get, create]
  - apiGroups: [""]
    resources: [services/proxy]
    verbs: [get, create]
  - apiGroups: [apps]
    resources: [deployments, statefulsets]
    verbs: [get, list, watch, create, patch, delete]
  - apiGroups: [networking.k8s.io]
    resources: [ingresses, networkpolicies]
    verbs: [get, list, watch, create, patch, delete]
  - apiGroups: [cert-manager.io]
    resources: [certificates]
    verbs: [get, list, watch]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata: {name: omnia-orchestrator-publisher}
roleRef: {apiGroup: rbac.authorization.k8s.io, kind: ClusterRole, name: omnia-orchestrator-publisher}
subjects:
  - {kind: ServiceAccount, name: omnia-orchestrator, namespace: max-system}
YAML
  local token ca wg_ip
  for _ in $(seq 1 20); do
    token=$(kubectl --context max-runtime -n max-system get secret omnia-orchestrator-token -o jsonpath='{.data.token}' 2>/dev/null | base64 -d || true)
    [ -n "$token" ] && break
    sleep 1
  done
  [ -n "$token" ] || { echo "token of omnia-orchestrator was not issued" >&2; exit 1; }
  ca=$(kubectl --context max-runtime -n max-system get secret omnia-orchestrator-token -o jsonpath='{.data.ca\.crt}')
  wg_ip=$(inv runtime wg)
  sh_remote core "install -d -m 711 /etc/max-studio; umask 077; cat > /etc/max-studio/runtime-kubeconfig.yaml <<KUBE
# generated by infra/max-k3s/k8s/apply.sh orchestrator-access — доступ оркестратора к кластеру runtime
apiVersion: v1
kind: Config
clusters:
  - name: max-runtime
    cluster:
      server: https://$wg_ip:6443
      certificate-authority-data: $ca
users:
  - name: omnia-orchestrator
    user:
      token: $token
contexts:
  - name: max-runtime
    context: {cluster: max-runtime, user: omnia-orchestrator}
current-context: max-runtime
KUBE
chown root:$ADMIN_USER /etc/max-studio/runtime-kubeconfig.yaml; chmod 640 /etc/max-studio/runtime-kubeconfig.yaml"
  # Проверка от имени самого оркестратора: kubectl на core — из k3s (sudo k3s kubectl), kubeconfig читаем как $ADMIN_USER.
  ssh -o BatchMode=yes "$(inv core alias)" "sudo k3s kubectl --kubeconfig /etc/max-studio/runtime-kubeconfig.yaml auth can-i create deployments -A && sudo k3s kubectl --kubeconfig /etc/max-studio/runtime-kubeconfig.yaml auth can-i delete namespaces | grep -q no" \
    && echo "  core: kubeconfig оркестратора установлен; создавать деплойменты — можно, удалять namespace — нет"
}

# Значения для оркестратора на core (реестр, kubeconfig, откуда качать артефакты). PUBLICATION_BACKEND
# не трогаем: перевод новых публикаций в кластер — отдельное осознанное действие
# (docs/plans/2026-09-23-k8s-publication-stage-a.md, I3).
phase_publication_env() {
  log "publication-env → /opt/omnia/apps/orchestrator/.env на core"
  local pw; pw=$(host_secret runtime registry.env REGISTRY_PASSWORD)
  run_remote core "$HERE/remote/60-publication-env.sh" "registry.$DOMAIN" "$REGISTRY_USER" "$pw" "http://$(inv core wg):8003" "apps.$DOMAIN" "$ADMIN_USER"
}

case "${1:-}" in
  coredns) phase_coredns ;;
  orchestrator-access) phase_orchestrator_access ;;
  publication-env) phase_publication_env ;;
  placeholder) phase_placeholder ;;
  cert-manager) phase_cert_manager ;;
  registry) phase_registry ;;
  registries-yaml) phase_registries_yaml ;;
  monitoring) phase_monitoring ;;
  all) phase_coredns; phase_cert_manager; phase_registry; phase_registries_yaml; phase_monitoring; phase_placeholder ;;
  *) sed -n 2,9p "$0"; exit 1 ;;
esac
