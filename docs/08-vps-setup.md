# 08. VPS setup для V2 Phase A runtime

Конкретные команды для подготовки `170.168.72.200` (Serverum VPS) к запуску orchestrator + dev-контейнеров пользователей.

**Перед началом** — прочитать `docs/07-v2-architecture.md` для контекста. Этот документ — последовательность shell-команд для **владельца сервера**. Любые операции из этого файла **запускает Артём вручную** — Claude Code не имеет SSH-доступа к prod.

## Pre-flight: что должно быть на VPS

```bash
# Ожидаемое состояние:
# - Ubuntu 22.04 LTS или новее
# - Docker 24+ (V1 уже использует, должен быть)
# - 4+ GB RAM (текущий VPS — проверить!)
# - 50+ GB disk free для контейнеров + образов
# - порты 80/443 открыты в firewall

ssh i48ptgvnis@170.168.72.200
uname -a
docker --version
docker compose version
free -h
df -h /
```

Если что-то < требуемого — апгрейд тарифа Serverum **до** V2 launch.

## Шаг 1. Создание директорий и пользователя

```bash
# Системный пользователь для orchestrator (изоляция от V1 user)
sudo useradd -m -s /bin/bash omnia-orchestrator
sudo usermod -aG docker omnia-orchestrator

# Структура каталогов под V2
sudo mkdir -p /opt/omnia-runtime/{projects,nginx,secrets,registry-data}
sudo chown -R omnia-orchestrator:omnia-orchestrator /opt/omnia-runtime
sudo chmod 700 /opt/omnia-runtime/secrets
```

## Шаг 2. Docker rootless (рекомендуется, но необязательно для бета)

Rootless Docker — defense in depth: если юзерский контейнер escape-нет sandbox, он попадёт в namespace юзера `omnia-orchestrator`, а не root.

```bash
# Только если ядро Linux 4.18+ и можно потерять часа 2 на отладку
# Для бета-запуска допустимо пропустить, вернуться после первых 10 проектов.
sudo apt install -y uidmap dbus-user-session
sudo -u omnia-orchestrator -i bash -c "
  dockerd-rootless-setuptool.sh install
"
```

Альтернатива (быстрее): оставить системный Docker, но запускать orchestrator под `omnia-orchestrator`, контейнеры — с `--user 1000:1000 --cap-drop=ALL`.

## Шаг 3. Local Docker registry (для prod deploys)

Когда юзер жмёт "Deploy" → orchestrator `docker build` → push в local registry → prod-контейнер pull-ит оттуда. Без registry build пришлось бы делать дважды (dev + prod).

```bash
cat > /opt/omnia-runtime/registry-compose.yml <<'EOF'
services:
  registry:
    image: registry:2
    container_name: omnia-registry
    restart: unless-stopped
    ports:
      - "127.0.0.1:5000:5000"   # ТОЛЬКО localhost — наружу не пускаем
    volumes:
      - ./registry-data:/var/lib/registry
    environment:
      REGISTRY_STORAGE_DELETE_ENABLED: "true"
EOF

sudo -u omnia-orchestrator docker compose -f /opt/omnia-runtime/registry-compose.yml up -d
```

## Шаг 4. Shared Postgres для user-проектов

Один Postgres-инстанс, одна схема на проект. Изоляция через `REVOKE ALL` + `GRANT` per-role.

```bash
cat > /opt/omnia-runtime/postgres-compose.yml <<'EOF'
services:
  postgres-users:
    image: postgres:16-alpine
    container_name: omnia-postgres-users
    restart: unless-stopped
    ports:
      - "127.0.0.1:5433:5432"   # 5432 занят V1 postgres
    environment:
      POSTGRES_USER: omnia_root
      POSTGRES_PASSWORD: ${POSTGRES_USERS_PASSWORD}   # из .env
      POSTGRES_DB: omnia_users
    volumes:
      - ./postgres-users-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U omnia_root"]
      interval: 5s
EOF

# Сгенерировать пароль
echo "POSTGRES_USERS_PASSWORD=$(openssl rand -hex 24)" > /opt/omnia-runtime/.env
chmod 600 /opt/omnia-runtime/.env

sudo -u omnia-orchestrator docker compose -f /opt/omnia-runtime/postgres-compose.yml --env-file /opt/omnia-runtime/.env up -d
```

Orchestrator при provision проекта будет:
```sql
CREATE SCHEMA "proj_<uuid>";
CREATE ROLE "proj_<uuid>_user" LOGIN PASSWORD '<random>';
GRANT USAGE ON SCHEMA "proj_<uuid>" TO "proj_<uuid>_user";
GRANT ALL ON ALL TABLES IN SCHEMA "proj_<uuid>" TO "proj_<uuid>_user";
REVOKE ALL ON SCHEMA public FROM "proj_<uuid>_user";  -- блокируем public schema
ALTER ROLE "proj_<uuid>_user" SET search_path = "proj_<uuid>";
```

## Шаг 5. Ingress nginx + Caddy для wildcard SSL

Caddy проще для Let's Encrypt wildcard через DNS-01 challenge — рекомендуется. Если уже nginx (V1), оставляем nginx + certbot DNS plugin.

### Вариант A — Caddy (рекомендуется для V2)

```bash
sudo apt install -y caddy

cat > /etc/caddy/Caddyfile <<'EOF'
# Wildcard cert через DNS-01 (требует API token провайдера домена)
*.omnia.app {
    tls {
        dns yandex_cloud {env.YANDEX_DNS_API_TOKEN}
    }

    @dev_subdomain {
        host {labels.0}-dev.omnia.app
    }

    handle @dev_subdomain {
        reverse_proxy /opt/omnia-runtime/ingress.sock unix
    }

    # Production subdomain
    reverse_proxy /opt/omnia-runtime/ingress.sock unix
}
EOF
```

**Note:** Caddy reverse_proxy идёт на unix socket — orchestrator слушает socket и роутит на правильный container port исходя из `<slug>`.

### Вариант B — nginx + certbot (если V1 уже на nginx)

Хранить wildcard cert в `/etc/letsencrypt/live/omnia.app/`. Orchestrator перегенерирует `/opt/omnia-runtime/nginx/sites-enabled/<slug>.conf` per проект.

```nginx
# /opt/omnia-runtime/nginx/sites-enabled/PROJ_SLUG.conf (auto-generated)
server {
    listen 443 ssl http2;
    server_name PROJ_SLUG-dev.omnia.app;

    ssl_certificate /etc/letsencrypt/live/omnia.app/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/omnia.app/privkey.pem;

    # HSTS — пробрасываем
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    location / {
        proxy_pass http://127.0.0.1:PROJ_PORT;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header Upgrade $http_upgrade;       # для HMR WebSocket
        proxy_set_header Connection "Upgrade";
        proxy_read_timeout 86400;                     # long-lived HMR
    }
}
```

## Шаг 6. Firewall

```bash
sudo ufw allow 22/tcp     # SSH
sudo ufw allow 80/tcp     # nginx HTTP (для redirect)
sudo ufw allow 443/tcp    # nginx HTTPS
# Внутренние порты НЕ открываем:
# - 5000 registry (localhost-only)
# - 5433 postgres-users (localhost-only)
# - 3001-3999 dev-контейнеры (только через nginx)
sudo ufw --force enable
```

## Шаг 7. Установка orchestrator-сервиса

После того как код orchestrator готов (apps/orchestrator/):

```bash
sudo -u omnia-orchestrator -i bash <<'EOF'
cd /opt/omnia-runtime
git clone https://github.com/Artem-Levchenko-hub/ConstrucorsitesAI.git source
cd source/apps/orchestrator
curl -LsSf https://astral.sh/uv/install.sh | sh
~/.local/bin/uv sync

# systemd unit для автозапуска
EOF

sudo tee /etc/systemd/system/omnia-orchestrator.service <<'EOF'
[Unit]
Description=Omnia.AI Orchestrator
After=docker.service postgresql.service
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

`.env.orchestrator` (chmod 600, owned by omnia-orchestrator):
```bash
DATABASE_URL=postgresql+asyncpg://omnia_root:<password>@127.0.0.1:5433/omnia_users
DOCKER_HOST=unix:///var/run/docker.sock
PROJECTS_ROOT=/opt/omnia-runtime/projects
NGINX_SITES_DIR=/opt/omnia-runtime/nginx/sites-enabled
SECRETS_ROOT=/opt/omnia-runtime/secrets
REGISTRY_URL=127.0.0.1:5000
BASE_DOMAIN=omnia.app
PORT_RANGE_MIN=3001
PORT_RANGE_MAX=3999
HIBERNATE_FREE_TIER_MINUTES=15
HIBERNATE_PRO_TIER_MINUTES=60
WAKE_TIMEOUT_SECONDS=60
```

## Шаг 8. API ↔ Orchestrator wiring

apps/api получает env `ORCHESTRATOR_URL=http://127.0.0.1:8003` — добавить в `infra/docker-compose.yml`:

```yaml
api:
  environment:
    ORCHESTRATOR_URL: ${ORCHESTRATOR_URL:-http://host.docker.internal:8003}
```

Поскольку orchestrator работает на хосте (вне docker-compose), api контейнер обращается через `host.docker.internal`.

## Шаг 9. DNS (домен `omnia.app` обязателен)

Купить `omnia.app` через Namecheap или Cloudflare ($14/год). DNS-провайдер должен поддерживать API для DNS-01 challenge (Let's Encrypt wildcard).

Рекомендация: Cloudflare DNS бесплатный + API token для DNS-01.

```bash
# A-record:
omnia.app           A    170.168.72.200
*.omnia.app         A    170.168.72.200
```

(Wildcard позволяет orchestrator-у не трогать DNS на каждый новый проект.)

## Шаг 10. Smoke test после установки

```bash
# Проверить что orchestrator живой
curl http://127.0.0.1:8003/health
# Ожидаем: {"status":"ok","docker":"connected","postgres":"connected"}

# Создать пробный проект через orchestrator API напрямую (минуя apps/api)
curl -X POST http://127.0.0.1:8003/internal/projects/provision \
  -H "X-Internal-Token: $(cat /opt/omnia-runtime/internal-token)" \
  -d '{"project_id":"00000000-0000-0000-0000-000000000001","slug":"test","template":"nextjs-postgres-drizzle"}'

# Через 60 сек:
curl https://test-dev.omnia.app
# Ожидаем: Next.js default landing
```

## Откат

Если что-то пошло не так — `sudo systemctl stop omnia-orchestrator`. V1 продолжает работать (отдельный docker-compose в `/opt/omnia-mvp`). V2 артефакты в `/opt/omnia-runtime` можно удалить (`docker compose down -v` + `rm -rf /opt/omnia-runtime/projects/*`).

## Что ниже radar

- **Backup пользовательских БД** — Phase A не покрыт; добавить в A4.
- **Logs aggregation** — пока systemd journal + docker logs. В A5 — Loki/Grafana.
- **Multi-VPS scaling** — когда упрёмся в 1 VPS (≈100 active проектов на 8GB RAM). План: добавить worker-ноды в `/opt/omnia-runtime/workers.yml`, orchestrator round-robin по нодам.
