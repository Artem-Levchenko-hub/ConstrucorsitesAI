#!/usr/bin/env bash
# Уровень Kubernetes (запускать с Mac; туннели поднимаются сами, kubeconfig ~/.kube/max-studio.yaml):
#   ./apply.sh cert-manager     # cert-manager + ClusterIssuer'ы Let's Encrypt (все три кластера)
#   ./apply.sh registry         # приватный Docker registry на runtime: https://registry.<домен>
#   ./apply.sh registries-yaml  # креды registry во все K3s (/etc/rancher/k3s/registries.yaml)
#   ./apply.sh monitoring       # kube-prometheus-stack на core: https://grafana.<домен>, node-exporter всех VPS
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
    sh_remote "$h" "install -d -m 700 /etc/max-studio; touch /etc/max-studio/$file; chmod 600 /etc/max-studio/$file; sed -i '/^$key=/d' /etc/max-studio/$file; echo '$key=$v' >> /etc/max-studio/$file"
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
  ingress:
    enabled: true
    ingressClassName: traefik
    annotations: {cert-manager.io/cluster-issuer: letsencrypt-prod}
    hosts: [grafana.$DOMAIN]
    tls: [{secretName: grafana-tls, hosts: [grafana.$DOMAIN]}]
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

case "${1:-}" in
  cert-manager) phase_cert_manager ;;
  registry) phase_registry ;;
  registries-yaml) phase_registries_yaml ;;
  monitoring) phase_monitoring ;;
  all) phase_cert_manager; phase_registry; phase_registries_yaml; phase_monitoring ;;
  *) sed -n 2,8p "$0"; exit 1 ;;
esac
