# 08. VPS setup для V2 Phase A runtime

Конкретные команды для подготовки `170.168.72.200` (Serverum VPS, hostname `inquisitive-head`) к запуску orchestrator + dev-контейнеров пользователей.

**Перед началом** — прочитать `docs/07-v2-architecture.md` для контекста. Любые операции из этого файла **запускает Артём вручную через SSH** — Claude Code не модифицирует прод напрямую.

## Текущее состояние сервера (snapshot на 2026-05-18)

Проверено через SSH-диагностику. На сервере уже живёт **много** проектов, V2 будет сосуществовать.

| Что | Сейчас | Замечание для V2 |
|---|---|---|
| OS | Debian 12 + kernel 6.1.170 | Rootless Docker поддерживается (kernel >= 4.18). |
| CPU / RAM | 8 cores / 15 GB (10 GB free) | На 10-15 dev-контейнеров по 512 MB хватит с запасом. |
| Disk | 516 GB total, 280 GB free | **HO 380 GB занято Docker мусором** — см. шаг 1 cleanup ниже. |
| Docker | 29.2.1 + Compose v5.1.0 | Свежий, OK. |
| User | `i48ptgvnis` в группах `docker`, `admin` | Sudo без пароля. orchestrator-user добавим отдельно. |
| Ingress | **системный nginx** на :80/443 (master PID — реальный, не контейнер) | Не Caddy — будем дописывать sites-enabled через orchestrator. |
| Существующие сайты (sites-enabled) | constructor.lead-generator.ru (V1 omnia prod) · omniadevelop.ru (waitlist landing) · app.lead-generator.ru · forum/site/yes/livekit.innertalk.* · messenger | V2 добавит `*.preview.omniadevelop.ru` + `*.app.omniadevelop.ru`. |
| Cert'ы Let's Encrypt | constructor.lead-generator.ru, app.lead-generator.ru, lead-generator.ru, innertalk.* | **omniadevelop.ru cert НЕТ** — выпустим в шаге 5. |
| V1 omnia | `/opt/omnia/` (репо) + контейнеры `omnia-prod-{web:3100, api:8200, gw:8101, worker, minio, postgres, redis}` | V2 в `/opt/omnia-runtime/` — отдельная папка. |
| Свободные порты для dev-контейнеров | 3001-3999 (заняты только 3030 endless-war, 3100 omnia-prod-web) | Port range orchestrator-а: 3200-3999 (избегаем существующих). |
| Параллельные проекты | endless-war (3 контейнера), messenger (4 контейнера), cs-stream | V2 контейнеры с префиксом `proj-<id>` — конфликтов имён не будет. |
| Soft warning V1 | `omnia-prod-web` помечен unhealthy, но логи показывают `Ready in 161ms`. Healthcheck `wget` ловит `Connection refused` — настоящий downtime отсутствует. | Фикс — отдельный V1 commit, не блокирует V2. |

## Шаг 0. Cleanup Docker мусора (обязательно ДО V2)

Docker накопил 177 GB образов (98% reclaimable) и 212 GB build cache. После cleanup освободится ~250 GB — необходимо для хранения per-project images.

```bash
ssh kanavto-vps
docker system df               # текущее состояние
docker system prune -af --volumes  # ВНИМАНИЕ: грохнет все unused images, builds, volumes
                                   # Запущенные контейнеры (V1 omnia, messenger, endless-war) НЕ трогаются.
docker system df               # должно показать <30 GB
```

После этого `df -h /` должен показать ~500 GB free.

## Шаг 1. Создание директорий и orchestrator-пользователя

```bash
# Системный пользователь для orchestrator (изоляция от i48ptgvnis)
sudo useradd -m -s /bin/bash omnia-orchestrator
sudo usermod -aG docker omnia-orchestrator

# Структура каталогов под V2 — отдельно от /opt/omnia/ (там V1)
sudo mkdir -p /opt/omnia-runtime/{projects,nginx,secrets,registry-data,postgres-users-data,logs}
sudo chown -R omnia-orchestrator:omnia-orchestrator /opt/omnia-runtime
sudo chmod 700 /opt/omnia-runtime/secrets

# Лог-каталог для systemd-юнита
sudo touch /var/log/omnia-orchestrator.log
sudo chown omnia-orchestrator:omnia-orchestrator /var/log/omnia-orchestrator.log
```

## Шаг 2. Docker rootless (рекомендуется, но необязательно для бета)

Rootless Docker — defense in depth: если юзерский контейнер escape-нет sandbox, он попадёт в namespace юзера `omnia-orchestrator`, а не root.

Для бета (первые 10-20 проектов) — допустимо пропустить и оставить системный Docker. Контейнеры всё равно стартуют с `--user 1000:1000 --cap-drop=ALL` (см. `core/docker_client.py`).

```bash
# Альтернатива для бета: НИКАКОЙ rootless setup, но строгие defaults в orchestrator.
# Полный rootless — позже, отдельным sprint'ом.
```

## Шаг 3. Local Docker registry

Когда юзер жмёт "Deploy" → orchestrator `docker build` → push в local registry → prod-контейнер pull-ит. Без registry build пришлось бы делать дважды.

```bash
cat > /opt/omnia-runtime/registry-compose.yml <<'EOF'
services:
  registry:
    image: registry:2
    container_name: omnia-registry
    restart: unless-stopped
    ports:
      - "127.0.0.1:5000:5000"   # localhost-only, наружу не пускаем
    volumes:
      - ./registry-data:/var/lib/registry
    environment:
      REGISTRY_STORAGE_DELETE_ENABLED: "true"
EOF

sudo -u omnia-orchestrator docker compose -f /opt/omnia-runtime/registry-compose.yml up -d
```

## Шаг 4. Shared Postgres для user-проектов

Отдельный Postgres от V1 — на :5433 (V1 omnia-prod-postgres держит :5432 у себя внутри Docker сети). Одна схема на проект, изоляция через `REVOKE ALL` + `GRANT`.

```bash
cat > /opt/omnia-runtime/postgres-compose.yml <<'EOF'
services:
  postgres-users:
    image: postgres:16-alpine
    container_name: omnia-postgres-users
    restart: unless-stopped
    ports:
      - "127.0.0.1:5433:5432"
    environment:
      POSTGRES_USER: omnia_root
      POSTGRES_PASSWORD: ${POSTGRES_USERS_PASSWORD}
      POSTGRES_DB: omnia_users
    volumes:
      - ./postgres-users-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U omnia_root"]
      interval: 5s
EOF

# Сгенерировать пароль
echo "POSTGRES_USERS_PASSWORD=$(openssl rand -hex 24)" | sudo tee /opt/omnia-runtime/.env
sudo chown omnia-orchestrator:omnia-orchestrator /opt/omnia-runtime/.env
sudo chmod 600 /opt/omnia-runtime/.env

sudo -u omnia-orchestrator docker compose \
  -f /opt/omnia-runtime/postgres-compose.yml \
  --env-file /opt/omnia-runtime/.env up -d
```

Orchestrator при provision проекта будет делать:
```sql
CREATE SCHEMA "proj_<uuid>";
CREATE ROLE "proj_<uuid>_user" LOGIN PASSWORD '<random>';
GRANT USAGE, CREATE ON SCHEMA "proj_<uuid>" TO "proj_<uuid>_user";
GRANT ALL ON ALL TABLES IN SCHEMA "proj_<uuid>" TO "proj_<uuid>_user";
REVOKE ALL ON SCHEMA public FROM "proj_<uuid>_user";
ALTER ROLE "proj_<uuid>_user" SET search_path = "proj_<uuid>";
```

## Шаг 5. Домен и wildcard SSL — `omniadevelop.ru`

В nginx sites-enabled уже зарегистрирован `omniadevelop.ru` (landing + waitlist), но SSL cert на него ещё не выпущен. Для V2 нам нужны:
- `*.preview.omniadevelop.ru` — для dev контейнеров (например, `myslug.preview.omniadevelop.ru`)
- `*.app.omniadevelop.ru` — для prod-деплоев (например, `myslug.app.omniadevelop.ru`)

### 5.1. DNS-записи у регистратора (где куплен omniadevelop.ru — REG.RU или Cloudflare)

```
A   omniadevelop.ru                     170.168.72.200    (уже есть, landing)
A   www.omniadevelop.ru                 170.168.72.200    (уже есть)
A   *.preview.omniadevelop.ru           170.168.72.200    ← добавить
A   *.app.omniadevelop.ru               170.168.72.200    ← добавить
```

Уровень wildcard — **второй** (`*.preview.X` и `*.app.X`). Let's Encrypt выдаёт wildcard на любом уровне, только через DNS-01 challenge.

### 5.2. Wildcard cert через certbot + DNS-01

certbot уже стоит на сервере (есть `/etc/letsencrypt/live/`). Нужен plugin под DNS-провайдера и API token.

Если домен в **Cloudflare**:
```bash
sudo apt install -y python3-certbot-dns-cloudflare
echo "dns_cloudflare_api_token = YOUR_TOKEN" | sudo tee /root/.cloudflare-omniadevelop.ini
sudo chmod 600 /root/.cloudflare-omniadevelop.ini

sudo certbot certonly \
  --dns-cloudflare \
  --dns-cloudflare-credentials /root/.cloudflare-omniadevelop.ini \
  -d "*.preview.omniadevelop.ru" \
  -d "*.app.omniadevelop.ru" \
  --agree-tos -m artem@omniadevelop.ru
```

Если домен в **REG.RU** — DNS-API более сложный, ребя предпочтительно делегировать DNS на Cloudflare (бесплатно, поддерживает API).

### 5.3. Catch-all nginx site для `*.preview.omniadevelop.ru`

Orchestrator пишет per-project conf в `/opt/omnia-runtime/nginx/sites-enabled/`. Но nginx сам должен включать эту директорию.

```bash
sudo tee /etc/nginx/conf.d/omnia-runtime.conf <<'EOF'
# V2 dev/prod subdomains served from orchestrator-managed configs.
include /opt/omnia-runtime/nginx/sites-enabled/*.conf;
EOF

sudo nginx -t && sudo systemctl reload nginx
```

Также catch-all для wildcard, чтобы default не падал:
```bash
sudo tee /etc/nginx/sites-available/omnia-preview-default <<'EOF'
server {
    listen 443 ssl http2;
    server_name *.preview.omniadevelop.ru *.app.omniadevelop.ru;
    ssl_certificate     /etc/letsencrypt/live/preview.omniadevelop.ru/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/preview.omniadevelop.ru/privkey.pem;

    # Если orchestrator не нашёл сайт для этого slug — 502 с подсказкой.
    location / { return 502 "project not provisioned or hibernated"; }
}
EOF
sudo ln -s /etc/nginx/sites-available/omnia-preview-default /etc/nginx/sites-enabled/
```

Per-project conf, который orchestrator генерирует:
```nginx
# /opt/omnia-runtime/nginx/sites-enabled/<slug>.conf — auto-generated
server {
    listen 443 ssl http2;
    server_name <slug>.preview.omniadevelop.ru;
    ssl_certificate     /etc/letsencrypt/live/preview.omniadevelop.ru/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/preview.omniadevelop.ru/privkey.pem;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    location / {
        proxy_pass http://127.0.0.1:<PORT>;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header Upgrade $http_upgrade;          # HMR WebSocket
        proxy_set_header Connection "Upgrade";
        proxy_read_timeout 86400;
    }
}
```

## Шаг 6. Firewall

Ничего не открывать новых портов наружу — только 80/443 (уже открыты).
```bash
sudo ufw status                          # проверка
# Все 3001-3999 dev-портов — localhost-only, наружу через nginx.
# Registry :5000 — localhost-only.
# Postgres-users :5433 — localhost-only.
```

## Шаг 7. Установка orchestrator-сервиса

```bash
sudo -u omnia-orchestrator -i bash <<'EOF'
cd /opt/omnia-runtime
git clone https://github.com/Artem-Levchenko-hub/ConstrucorsitesAI.git source
cd source
git checkout claude/v2-phase-a-fullstack    # пока V2 не в main
cd apps/orchestrator
curl -LsSf https://astral.sh/uv/install.sh | sh
~/.local/bin/uv sync
EOF

# Создать env-файл
sudo tee /opt/omnia-runtime/.env.orchestrator <<EOF
ENV=prod
LOG_LEVEL=INFO
DATABASE_URL=postgresql+asyncpg://omnia_root:$(grep POSTGRES_USERS_PASSWORD /opt/omnia-runtime/.env | cut -d= -f2)@127.0.0.1:5433/omnia_users
DOCKER_HOST=unix:///var/run/docker.sock
PROJECTS_ROOT=/opt/omnia-runtime/projects
NGINX_SITES_DIR=/opt/omnia-runtime/nginx/sites-enabled
SECRETS_ROOT=/opt/omnia-runtime/secrets
REGISTRY_URL=127.0.0.1:5000
BASE_DOMAIN=omniadevelop.ru
PORT_RANGE_MIN=3200
PORT_RANGE_MAX=3999
HIBERNATE_FREE_TIER_MINUTES=15
HIBERNATE_PRO_TIER_MINUTES=60
WAKE_TIMEOUT_SECONDS=60
INTERNAL_TOKEN=$(openssl rand -hex 32)
SENTRY_DSN=
EOF
sudo chown omnia-orchestrator:omnia-orchestrator /opt/omnia-runtime/.env.orchestrator
sudo chmod 600 /opt/omnia-runtime/.env.orchestrator

# systemd unit
sudo tee /etc/systemd/system/omnia-orchestrator.service <<'EOF'
[Unit]
Description=Omnia.AI Orchestrator
After=docker.service
Requires=docker.service

[Service]
Type=simple
User=omnia-orchestrator
WorkingDirectory=/opt/omnia-runtime/source/apps/orchestrator
EnvironmentFile=/opt/omnia-runtime/.env.orchestrator
ExecStart=/home/omnia-orchestrator/.local/bin/uv run uvicorn omnia_orchestrator.main:app --host 127.0.0.1 --port 8003
Restart=on-failure
RestartSec=5
StandardOutput=append:/var/log/omnia-orchestrator.log
StandardError=append:/var/log/omnia-orchestrator.log

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now omnia-orchestrator
sudo systemctl status omnia-orchestrator
```

### Шаг 7.1. Хардненинг юнита — два обязательных фикса (иначе превью не работает)

> На проде юнит запущен под `i48ptgvnis` (в группе `docker`) и **захардненен**
> сверх шаблона выше: `NoNewPrivileges=true`, `ProtectSystem=full`,
> `ProtectHome=read-only`, `ReadWritePaths=/opt/omnia-runtime`. Эти директивы
> **конфликтуют с дизайном** оркестратора (ему нужен `sudo nginx` и запись
> acme-ключей) и тихо ломают per-project preview-домены для ВСЕХ container-
> шаблонов. Если ставишь юнит с таким хардненингом — применить оба фикса.
> (Без хардненинга, как в шаблоне Шага 7, фиксы не нужны.)

**Фикс 1 — `NoNewPrivileges=false` (nginx reload).** `NoNewPrivileges` блокирует
`sudo -n nginx -t/-s reload`, без которого не публикуется preview-домен. Сервис
и так root-эквивалентен через docker-сокет, так что NNP тут почти не даёт
защиты. Снять через drop-in override (не правя основной юнит):

```bash
sudo mkdir -p /etc/systemd/system/omnia-orchestrator.service.d
sudo tee /etc/systemd/system/omnia-orchestrator.service.d/override.conf <<'EOF'
[Service]
NoNewPrivileges=false
EOF
sudo systemctl daemon-reload
sudo systemctl restart omnia-orchestrator
# проверка: должно быть `NoNewPrivileges=no`
systemctl show -p NoNewPrivileges omnia-orchestrator
```

Также нужно sudoers-правило NOPASSWD для `nginx` и `systemctl reload nginx`
(на проде это покрыто широким passwordless-sudo юзера `i48ptgvnis`; для
выделенного `omnia-orchestrator` — добавить `/etc/sudoers.d/omnia-orchestrator`).

**Фикс 2 — acme-home в writable путь (HTTPS-сертификаты).** acme.sh по умолчанию
пишет аккаунт/ключи в `~/.acme.sh`, который `ProtectHome=read-only` делает
недоступным на запись → `Cannot create domain key` → нет per-project HTTPS-cert
(а preview-iframe в workspace загружается только по валидному HTTPS). Оркестратор
(`nginx_writer.py`) уже передаёт acme `--home $OMNIA_ACME_HOME`
(default `/opt/omnia-runtime/acme-home` — это writable `ReadWritePaths`). Нужен
**one-time seed** этого каталога из существующей установки acme.sh, чтобы
перенёсся Let's Encrypt-аккаунт (иначе re-registration + риск rate-limit):

```bash
# из-под юзера, под которым крутится сервис (на проде — i48ptgvnis):
cp -a ~/.acme.sh /opt/omnia-runtime/acme-home
# при желании сменить путь — добавить в .env.orchestrator:
#   OMNIA_ACME_HOME=/opt/omnia-runtime/acme-home
```

> Проверка обоих фиксов: provision пробного проекта → за ~15–20 с в логе
> оркестратора cert выписывается без `Cannot create domain key`, а
> `curl --resolve <host>:443:127.0.0.1 https://<host>/` отдаёт `200`
> (`ssl_verify=0`). `curl https://<host>` С САМОГО VPS даёт `000` — это
> DNS-hairpin (VPS не ходит на свой публичный IP), снаружи всё работает.

**Прочие env-оверрайды оркестратора** (defaults в коде, переопределять только
при нестандартной инфре): `OMNIA_RUNTIME_NETWORK` (default `omnia-runtime_default`
— docker-сеть, где живёт `omnia-postgres-users`; user-контейнеры цепляются к ней
и ходят в БД по имени), `OMNIA_RUNTIME_DB_HOST` / `OMNIA_RUNTIME_DB_PORT`
(default `omnia-postgres-users` / `5432` — контейнерный DSN, container-to-container,
а НЕ через host-бинд `127.0.0.1:5433`, который из контейнера недостижим).

## Шаг 8. Подключение apps/api к orchestrator

V1 контейнер `omnia-prod-api` (на :8200) должен знать, как достучаться до orchestrator (на host'е :8003). Через `host.docker.internal`:

```bash
# Добавить env в docker-compose (на сервере: /opt/omnia/infra/docker-compose.yml)
# Шаблон патча:
#   environment:
#     ORCHESTRATOR_URL: http://host.docker.internal:8003
#     ORCHESTRATOR_INTERNAL_TOKEN: <тот же что в .env.orchestrator>
#
# Restart api:
docker restart omnia-prod-api
```

⚠️ `host.docker.internal` работает в современных Docker (29.x) — должен резолвиться напрямую. Если нет, использовать IP моста docker (`docker network inspect bridge`).

## Шаг 9. Smoke test после установки

```bash
# Orchestrator alive?
curl http://127.0.0.1:8003/health
# Ожидаем: {"status":"ok"}

# Provision пробного проекта через orchestrator API напрямую (минуя apps/api):
TOKEN=$(grep INTERNAL_TOKEN /opt/omnia-runtime/.env.orchestrator | cut -d= -f2)
curl -X POST http://127.0.0.1:8003/internal/projects/provision \
  -H "X-Internal-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"project_id":"00000000-0000-0000-0000-000000000001","slug":"test","template":"nextjs-postgres-drizzle"}'

# Пока тут будет 501 — sprint A1 ещё не реализован. Это нормально.

# После реализации sprint A1:
curl https://test.preview.omniadevelop.ru
# Ожидаем: Next.js default landing (если cold start завершён за <60 сек)
```

## Откат

Если что-то пошло не так — `sudo systemctl stop omnia-orchestrator`. V1 продолжает работать (его контейнеры `omnia-prod-*` независимы). V2 артефакты в `/opt/omnia-runtime` можно удалить (`docker compose down -v` + `rm -rf`).

## Что не покрыто (Phase A.5+)

- **Backup пользовательских БД** — добавится в sprint A4.
- **Loki/Grafana** для аггрегации логов dev-контейнеров — sprint A5.
- **Multi-VPS scaling** — когда упрёмся в 1 VPS (≈30 active проектов на 15 GB RAM с учётом V1 + other tenants). План: worker-ноды + orchestrator round-robin.
- **Wildcard cert renewal automation** — certbot renew по cron, но reload nginx после renew.
