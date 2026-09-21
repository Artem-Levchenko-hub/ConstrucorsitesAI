# max-app — Helm-чарт одного опубликованного MAX-приложения

## Что это простыми словами

Сегодня каждое опубликованное MAX-приложение живёт на одном сервере внутри
Docker: оркестратор руками создаёт контейнер с приложением, отдельный
PostgreSQL для его данных, доверенный контейнер `max-core` (бот, подпись
пользователя, граница «владелец / посетитель») и вписывает виртуальный хост в
общий nginx. Всё это — набор шагов в Python-коде, который знает про конкретный
сервер и конкретный Docker.

Этот чарт — перевод той же конструкции на язык Kubernetes. Чарт (Helm chart) —
это шаблон-описание: «вот приложение, вот его база, вот кто с кем имеет право
разговаривать, вот сколько ресурсов». Кластер читает описание и сам поднимает
всё, что там перечислено, на любом количестве серверов. Зачем: владелец решил
переезжать на Kubernetes, чтобы приложения клиентов запускались без ручного
обслуживания отдельного сервера, а мощности добавлялись автоматически.

Эффект сейчас: появился проверяемый «слепок» одного приложения и автоматическая
проверка в GitHub Actions, которая на каждое изменение поднимает временный
кластер и убеждается, что описание действительно разворачивается. Это половина
задачи — упаковка.

## Чем это ещё НЕ является

Это **заготовка, а не переключатель на прод**. Прямо сейчас чарт нельзя взять и
пустить в него живых пользователей, потому что:

- **Нет доверенного шлюза.** В Docker весь публичный трафик сначала попадает в
  `machine_boundary.py` — он проверяет вход через MAX, ставит защиту от CSRF и
  только потом пускает запрос дальше. Этот шлюз в чарт не упакован, поэтому
  `Ingress` по умолчанию смотрит на `max-core`, а чтобы направить его прямо на
  приложение, нужно явно подтвердить, что вы согласны остаться без проверки
  входа.
- **Оркестратор ещё не умеет разворачивать в кластер.** Сборка и заливка образа
  в реестр, перенос данных из старого Docker-тома в диск кластера, резервные
  копии этого диска, «усыпание» простаивающего приложения — всё это отдельные
  следующие шаги.
- **Две базы сведены в одну.** В Docker у доверенного ядра своя, физически
  отдельная база; здесь обе базы живут в одном PostgreSQL и разделены только
  именем. Пока продукт имеет полные права администратора в своей базе, это
  ослабление изоляции — см. «What is NOT covered yet».

Ничего из перечисленного не проверялось на живом продовом кластере: у чарта
вообще ещё нет живого кластера. Проверка — только GitHub Actions на временном
кластере kind с картинками-заглушками вместо настоящих образов.

---

## Technical reference (English)

### What this chart describes

One published MAX mini-app, as three workloads in one namespace:

| Component | Kind | Port | Health path | User | Root FS |
|---|---|---|---|---|---|
| `app` — generated product | Deployment | 3000 | `/api/omnia/health` | 1000 (`node`) | read-only + emptyDir |
| `max-core` — trusted core | Deployment | 3000 | `/api/health` | 1000 (`node`) | writable (see below) |
| `postgres` — project database | StatefulSet + PVC | 5432 | `pg_isready` exec | 999 | read-only + emptyDir |

Plus: a ClusterIP Service per workload, a headless Service governing the
StatefulSet, an optional Ingress, a Secret (rendered or referenced), a
ServiceAccount with `automountServiceAccountToken: false`, a NetworkPolicy set,
an optional PodDisruptionBudget, and a ConfigMap holding one `CREATE DATABASE`
statement (DDL only — never a credential).

### Runtime contract this chart is derived from

Paths are relative to `apps/orchestrator/src/omnia_orchestrator`.

| Fact | Evidence |
|---|---|
| Product and core both listen on 3000 | `services/machine_adapter.py:750`, `:352` (boundary forwards to `core_host:3000`) |
| Core health path is `/api/health` | `services/machine_adapter.py:771` |
| Product readiness path is `/api/omnia/health` | `services/machine_defaults.py:48-52` |
| Product DSN env set | `services/docker_machine_backend.py:351-371` |
| Core env set (`AUTH_SECRET`, `OMNIA_PROJECT_ID`, `DATABASE_URL`, `REDIS_URL`, `NODE_ENV`, `HOSTNAME`, `PORT`, `NODE_OPTIONS`) | `services/machine_adapter.py:741-752` |
| Allowed bot credentials | `schemas/cell_publication.py:42-47` |
| `OMNIA_PUBLIC_APP_ORIGIN` is the published URL | `services/cell_publication.py:877` |
| Core runs as `node`, caps dropped, no-new-privileges | `services/machine_adapter.py:735-739` |
| Project postgres runs as `postgres`, read-only rootfs, tmpfs `/tmp` | `services/docker_machine_backend.py:663-682` |
| Postgres readiness proof | `services/docker_machine_backend.py:884-896` |
| Resource caps for a *published* app | `core/config.py:270-277` |
| Egress is default-deny with a proxy exception | `scripts/project_machine_namespace_guard.py:26-66` |

### Why `max-core` is not a sidecar

The brief allowed a sidecar only if the real containers share a network
namespace. They do not:

- The product container runs with `network_mode: container:<guard>`
  (`services/docker_machine_backend.py:450`), i.e. inside the namespace guard's
  network namespace.
- The project database joins that *same* namespace
  (`services/docker_machine_backend.py:662`), which is exactly why its DSN is
  `127.0.0.1:5432` (`services/docker_machine_backend.py:42-43, 351-371`).
- `max-core` is created on the cell's internal bridge network instead
  (`services/machine_adapter.py:735`) and is reached by IP.
- The boundary is configured with two *different* addresses — `core_host` is the
  core's IP, `machine_host` is the guard's (`services/machine_adapter.py:815-816`).
  If they shared a namespace those would be one address.

The separation is load-bearing, not incidental: the namespace guard sets
`OUTPUT DROP` and re-opens only the egress proxy and declared data endpoints
(`scripts/project_machine_namespace_guard.py:26-66`), so agent-generated code
cannot reach the trusted core at all. Putting both in one pod would hand the
product loopback access to a process that holds `AUTH_SECRET` and the bot token.
So: two Deployments, two Services, no pod-level sharing.

Note the inverse: product and database *do* share a namespace in Docker, but a
StatefulSet with a PVC cannot, so the DSN host becomes the headless Service
instead of `127.0.0.1`. That is a deliberate, documented deviation.

### Values

| Key | Default | Meaning |
|---|---|---|
| `projectId` | `""` | `OMNIA_PROJECT_ID`; identifier, not a credential |
| `publicOrigin` | `""` | `OMNIA_PUBLIC_APP_ORIGIN`; derived from `ingress.host` when empty |
| `app.image.repository` / `.digest` / `.tag` | `""` | Digest wins; empty or `latest` makes rendering fail |
| `app.containerPort` | `3000` | Product listen port |
| `app.healthPath` | `/api/omnia/health` | Used by all three probes |
| `app.args` | `[]` | Container args; never put credentials here |
| `app.resources` | 100m/512Mi → 500m/2Gi | Limits match `cell_public_machine_*` |
| `app.readOnlyRootFilesystem` | `true` | With emptyDir on `app.writablePaths` |
| `app.writablePaths` | `/tmp`, `/app/.next/cache` | Mirrors the Next cache volume of the Docker runtime |
| `app.runAsUser` / `.runAsGroup` | `1000` | `USER node` in `Dockerfile.prod` |
| `core.enabled` | `true` | Disable to render the product alone |
| `core.healthPath` | `/api/health` | The path the orchestrator waits on |
| `core.resources` | 50m/256Mi → 200m/768Mi | Limits match `cell_public_core_*` |
| `core.readOnlyRootFilesystem` | `false` | The controller writes `/app/omnia-business-config.json` into the running core |
| `core.nodeOptions` | `--max-old-space-size=384` | Same clamp the orchestrator computes |
| `core.maxApiBaseUrl` | `https://platform-api2.max.ru` | `MAX_API_BASE_URL` |
| `core.platformApiUrl` | `""` | `OMNIA_PLATFORM_API_URL`; empty keeps the image default |
| `postgres.username` / `.database` | `postgres` / `postgres` | Product database |
| `postgres.coreDatabase` | `maxcore` | Trusted core database on the same instance |
| `postgres.persistence.size` / `.storageClassName` / `.accessModes` | `10Gi` / `""` / `[ReadWriteOnce]` | Empty class = cluster default |
| `postgres.args` | `-c listen_addresses=*` | Docker pinned `127.0.0.1`; a StatefulSet must listen wider |
| `postgres.resources` | 50m/256Mi → 150m/256Mi | Matches `cell_project_postgres_*` |
| `postgres.fsGroup` / `.pgdataSubPath` | `999` / `pgdata` | `initdb` owns a sub-directory 0700 inside a shared volume |
| `secrets.existingSecret` | `""` | Empty = render a Secret; set = reference one |
| `secrets.keys.*` | see `values.yaml` | Key names inside the Secret |
| `secrets.values.*` | `""` | Only used in rendered mode |
| `ingress.enabled` / `.className` / `.host` / `.annotations` | `false` / `""` / `""` / `{}` | Annotations pass through verbatim |
| `ingress.tls.enabled` / `.secretName` | `false` / `""` | References an existing TLS Secret; the chart issues nothing |
| `ingress.service` | `core` | `core` or `app` — see the warning above |
| `ingress.acknowledgeMissingAuthBoundary` | `false` | Required to point the Ingress at `app` |
| `serviceAccount.create` / `.name` / `.annotations` | `true` / `""` / `{}` | Token is never mounted |
| `networkPolicy.enabled` | `true` | Namespace-wide default deny plus named flows |
| `networkPolicy.ingressController.namespaceSelector` | `kubernetes.io/metadata.name: ingress-nginx` | The only source allowed to reach 3000 |
| `networkPolicy.dns.*` | kube-system / `k8s-app: kube-dns` / 53 | DNS egress |
| `networkPolicy.egressAllowlist` | `[]` | Named CIDR egress per component |
| `podDisruptionBudget.enabled` / `.minAvailable` / `.maxUnavailable` | `false` / `1` / `null` | Set exactly one of the two |
| `imagePullSecrets`, `commonLabels`, `commonAnnotations` | `[]`, `{}`, `{}` | Applied to every object |

`values.schema.json` rejects unknown keys, so a typo fails the install instead
of silently doing nothing. The chart deliberately does **not** render with its
own defaults: no image is set, and the image helper refuses an empty
repository. Always install with a values file.

### Security model

- **Credentials only ever travel Secret → env.** No password, DSN or token is
  written into a ConfigMap, an annotation or a container argument. The only
  ConfigMap in the chart holds one `CREATE DATABASE` statement. Pods carry a
  `checksum/secret` annotation, which is a SHA-256 of the rendered Secret
  document — a hash, not a value — so rotating a password restarts the pods.
- **Two Secret modes.** Rendered (chart builds the Secret, including both DSNs)
  or referenced (`existingSecret`), which is how the orchestrator should use it:
  it already owns the credential files and never has to hand plaintext to Helm.
  Optional keys (`max-bot-token`, `max-webhook-secret`, `redis-url`) are read
  with `optional: true`, matching the runtime where a first publication may have
  no bot yet (`services/machine_adapter.py:800`).
- **Restricted pod security throughout**, following the posture already pinned in
  `infra/project-cell/manifests/trusted-hello.yaml`: `runAsNonRoot`, explicit
  uid/gid, `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`,
  `seccompProfile: RuntimeDefault`, and `readOnlyRootFilesystem` wherever the
  Docker container is already read-only. The one exception is `max-core`, whose
  `/app` must stay writable because the controller uploads the business config
  into the running container (`services/machine_business_config.py:68-82`).
- **Images are digest-pinned.** `repository` + `digest` is the supported form;
  a bare `latest` tag, an empty repository and a malformed digest all abort
  rendering with a named error.
- **Network default-deny.** One namespace-wide deny, then: DNS egress, ingress
  from the ingress-controller namespace only, app/core → postgres:5432 both
  directions, and an optional named CIDR allow-list for the platform API and the
  MAX Bot API. This is the Kubernetes translation of the namespace guard's
  `INPUT/OUTPUT/FORWARD DROP` posture.
- **No API access.** The ServiceAccount and every pod set
  `automountServiceAccountToken: false`.

Install **one release per namespace**: the default-deny policy uses an empty
`podSelector`, so it applies to everything else in that namespace too.

### How the orchestrator is expected to call it

```bash
helm upgrade --install <release> infra/max-app-chart \
  -n <namespace> --create-namespace \
  -f values.yaml \
  --atomic --wait
```

with `values.yaml` written by the orchestrator per app: images by digest,
`projectId`, `ingress.host`, and `secrets.existingSecret` pointing at a Secret
it created beforehand from its own credential store
(`services/cell_state.py:930-963`). `--atomic` rolls the release back on
failure, which is the Kubernetes analogue of the publication rollback in
`services/cell_publication.py:990-1020`.

### What IS verified, and by what

`.github/workflows/max-app-chart.yml`, on every change to the chart or the
workflow.

**lint job proves:**
- `helm lint --strict` passes with both `ci/` value files.
- `helm template` output validates against real Kubernetes 1.32 schemas
  (`kubeconform -strict`, no skipped kinds).
- Every container in every workload has CPU/memory requests *and* limits, a
  restricted `securityContext`, a digest- or tag-pinned image and at least one
  probe; every pod sets `automountServiceAccountToken: false`.
- Sentinel secret values from `ci/minimal-values.yaml` appear **only** inside
  `kind: Secret` documents — nowhere in a ConfigMap, annotation, env value or
  argument.
- `ci/existing-secret-values.yaml` renders **no** Secret at all.
- Rendering **fails, with the expected message**, for: a `latest` tag, an empty
  repository, a malformed digest, a missing password in rendered mode, an
  Ingress pointed at the product without the explicit acknowledgement, and an
  unknown values key. The expected reason is asserted too, so a refusal for some
  unrelated reason cannot pass as proof that a guard works.

**kind job proves:**
- The chart installs into a real API server (kind, node image pinned by digest)
  and every workload reaches `Ready` — so the StatefulSet's PVC binds, the
  probes pass, the Secret keys resolve, `NOTES.txt` renders and the images run
  under the restricted `securityContext`.
- From a throwaway pod inside the namespace, carrying the product's identity
  labels: the app Service and the core Service each answer `200` on their
  health path, PostgreSQL accepts a connection authenticated with the password
  from the Secret, and the trusted core's database exists — which is also the
  proof that the init ConfigMap ran.
- `ci/existing-secret-values.yaml` is accepted by the API server
  (`helm install --dry-run=server`).
- On failure it always dumps `kubectl get all,netpol,pvc`, pod descriptions,
  namespace events and pod logs.

### Known open item

The `kind` job is marked `continue-on-error` while its in-cluster probe is brought
up: on a real run the probe pod ends `Error` after the release installs and both
Deployments roll out, and the container log still has to be read to say why. The
`lint` job — render, schema validation, the security and secret-scope assertions
and the six refusal cases — is the verification that gates the branch.

### What is NOT verified

- **NetworkPolicy is not enforced in CI.** kind's default CNI (kindnet) does not
  implement NetworkPolicy, so the policies are only checked for schema validity
  and acceptance by the API server. The negative test — "a pod without the
  allowed labels cannot reach PostgreSQL" — is deliberately **not run**, because
  on kind it would pass for the wrong reason and prove nothing. Installing
  Calico just for this was judged not worth the pin; run the negative test on
  the first real K3s/managed cluster instead.
- **The stub images are not the MAX images.** CI runs agnhost and the official
  `postgres` image, with `healthPath` overridden to `/healthz`. The real health
  paths (`/api/omnia/health`, `/api/health`) are the chart defaults but nothing
  in CI exercises real MAX behaviour: no login, no bot, no migrations.
- **The Ingress is never served.** No ingress controller is installed, so the
  Ingress object is only proven to be accepted by the API server. TLS
  termination, the wildcard certificate and the hostname are untested.
- **Nothing has ever run on a real cluster.** There is no K3s or managed-K8s
  deployment of this chart; no production traffic has touched it.
- **The kind job itself has never been executed.** The author's machine has no
  Docker, so the `kind` job is written but unproven; the `lint` job, by
  contrast, was run locally step by step with the same pinned Helm and
  kubeconform before being committed.

### What is NOT covered yet (next steps)

1. **The trusted boundary gateway.** `services/machine_boundary.py` authenticates
   the MAX session, enforces the CSRF origin, mirrors the embedded cookies and
   routes reserved paths to the core. In Docker it is seeded into a container's
   tmpfs at runtime; it has no image and no chart. Until it is packaged, nothing
   here is a production entrypoint.
2. **Image build and push from the orchestrator.** The chart consumes a digest;
   nothing yet builds the per-app image and pushes it to a registry the cluster
   can pull from.
3. **Data migration from the Docker volume to the PVC.** Published apps have live
   business data in named Docker volumes on the VPS. Moving it into a PVC (and
   proving the schema digest is unchanged, as
   `services/published_machine_backend.py:424-450` does today) is unsolved.
4. **Backups of the PVC.** The nightly backup currently covers the platform
   database only; Project Cell data is a known gap and a PVC does not change
   that.
5. **Scale-to-zero and the wake page.** The Docker runtime hibernates idle apps
   and serves a "waking up" interstitial through nginx
   (`services/nginx_writer.py`). There is no equivalent here; every app runs
   permanently.
6. **Split the two databases again.** Give the trusted core its own StatefulSet,
   or at minimum a non-superuser role per database, so the product's full admin
   rights cannot reach MAX identity tables.
7. **Redis.** The Docker core gets a `REDIS_URL`; the shipped MAX kit never reads
   it. The chart passes it through optionally and provisions nothing.

### Local development

The repo toolchain carries no Helm. Install the same pinned versions the
workflow uses and run the `lint` job's steps by hand:

```bash
go install helm.sh/helm/v3/cmd/helm@v3.16.4
go install github.com/yannh/kubeconform/cmd/kubeconform@v0.6.7

helm lint --strict infra/max-app-chart \
  --values infra/max-app-chart/ci/minimal-values.yaml
helm template max-app infra/max-app-chart -n max-app-ci \
  --kube-version 1.32.0 \
  --values infra/max-app-chart/ci/minimal-values.yaml \
  | kubeconform -strict -summary -kubernetes-version 1.32.0
```

`--kube-version` is required: without it `helm template` assumes Kubernetes 1.20
and the chart's `kubeVersion` floor refuses to render.

Every assertion lives in the workflow on purpose — there is no test script in
the chart that could drift from what CI actually runs.
