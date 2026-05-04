# Gateway deployment runbook (single-server demo)

**Цель:** поднять Omnia LLM Gateway на свежем VPS за ~10 минут так, чтобы наружу торчал `http://<server-ip>:8001/v1/chat/completions`.

**Стек:** Docker + docker-compose, Postgres 16, Redis 7, gateway-контейнер. Без HTTPS, без домена — это demo. Под прод накатить Caddy/Nginx + Let's Encrypt отдельной фазой.

---

## 0. Безопасность ДО первого захода

1. После первого SSH **смени пароль** (`passwd`) — стартовый из панели хостинга считается засветившимся.
2. Сразу же создай SSH-ключ локально (если ещё нет) и добавь его на сервер; пароль для `ssh` потом отключи.
3. На VPS-хостинге (Serverum) открой в файрволле порт `8001/tcp` (демо) или `80/tcp + 443/tcp` (если ставишь Caddy).

---

## 1. На своей машине: SSH-ключ (одноразово)

```bash
# если ключа ещё нет:
ssh-keygen -t ed25519 -C "omnia-deploy"

# скинуть публичный ключ на сервер (введёшь пароль один раз):
ssh-copy-id i48ptgvnis@170.168.72.200
# Windows без ssh-copy-id:
#   cat ~/.ssh/id_ed25519.pub | ssh i48ptgvnis@170.168.72.200 'mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys'

# проверить вход без пароля:
ssh i48ptgvnis@170.168.72.200 'echo OK'
```

После этого в `/etc/ssh/sshd_config` поставь `PasswordAuthentication no` и перезапусти ssh — пароль больше не нужен.

---

## 2. На сервере: установка Docker (одноразово)

```bash
ssh i48ptgvnis@170.168.72.200

# Только если docker ещё не стоит:
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
# выйти и снова зайти, чтобы группа подтянулась:
exit
ssh i48ptgvnis@170.168.72.200

docker --version
docker compose version
```

---

## 3. Клонирование репозитория

```bash
sudo mkdir -p /opt/omnia && sudo chown $USER:$USER /opt/omnia
cd /opt/omnia
git clone https://github.com/Artem-Levchenko-hub/ConstrucorsitesAI.git .
cd apps/llm-gateway
```

---

## 4. Настройка `.env` (с реальным GigaChat ключом)

```bash
cp .env.example .env
nano .env   # или vim, или vi
```

Минимум, что должно быть заполнено для демо со Sber:

```ini
GIGACHAT_AUTH_KEY=<длинная base64 строка из кабинета developers.sber.ru>
GIGACHAT_SCOPE=GIGACHAT_API_PERS
GIGACHAT_VERIFY_SSL=false

# Эти URL переопределит docker-compose, оставь как есть:
DATABASE_URL=postgresql://omnia:omnia@localhost:5432/omnia
REDIS_URL=redis://localhost:6379/1
```

> **Важно:** `.env` лежит в `.gitignore` (двойная защита: корневой и gateway-овский) — он не уйдёт в git.

---

## 5. Поднять стек

```bash
docker compose -f docker-compose.demo.yml up -d --build
```

При первом запуске собирается образ gateway (~2 мин), стартуют postgres + redis, init.sql применяется автоматически.

Проверить состояние:

```bash
docker compose -f docker-compose.demo.yml ps
# все три должны быть в state "running" / healthy
```

Логи gateway:

```bash
docker compose -f docker-compose.demo.yml logs -f gateway
```

---

## 6. Smoke-тесты

С самого сервера:

```bash
curl -s http://localhost:8001/health
# {"status":"ok"}

curl -s http://localhost:8001/v1/models | python3 -m json.tool
# должны быть 9 моделей; gigachat-* с available=true
```

С локалки:

```bash
curl http://170.168.72.200:8001/health
curl -X POST http://170.168.72.200:8001/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"gigachat-2","messages":[{"role":"user","content":"привет"}]}'
```

---

## 7. Обновление после нового коммита

```bash
cd /opt/omnia
git pull
cd apps/llm-gateway
docker compose -f docker-compose.demo.yml up -d --build
```

Без потери данных — postgres/redis volumes сохраняются между перезапусками.

---

## 8. Что дальше для прода

- **HTTPS:** добавить Caddy перед gateway (пара строк в compose + `Caddyfile` с `your-domain.tld`); LE-сертификат поднимается автоматически.
- **Биллинг:** сейчас pre-check кошелька включится автоматически если в `users` + `wallets` есть юзер с положительным балансом (см. `init.sql`). В реальном flow таблицы заполняет `apps/api` агента B.
- **Объединение с api+worker+minio:** агент B расширит `infra/docker-compose.yml` сервисом `llm-gateway`, и тогда демо-стек тут больше не нужен — единый стек подхватит наш Dockerfile.
- **Ротация ключей:** GIGACHAT_AUTH_KEY в `.env` — если уйдёт нагрузка от user-ов, перевыпустить через панель Sber и заменить на сервере одной командой `nano .env && docker compose ... up -d gateway`.
- **Мониторинг:** структурные логи уже JSON-line в stdout (structlog) → `docker logs` собирает их; можно навесить promtail+Loki или просто `docker compose logs --tail 100`.

---

## Откат

Полный сброс (выкосить всё, включая БД):

```bash
docker compose -f docker-compose.demo.yml down -v
```

Без `-v` — оставить volumes (постгрес+редис данные сохранятся).
