# Окно обслуживания: шаги 4.4 и 4.5 ребрендинга — контейнеры, образы, пути, юниты

Окно разрешено владельцем 25.09.2026. Этот документ — то, по чему окно выполняется и
откатывается. План целиком: [`2026-09-25-yleum-rebrand.md`](../plans/2026-09-25-yleum-rebrand.md).

**Предусловие: волна 4.1–4.3 выкачена и проверена.** Переименовывать контейнеры и пути
поверх невыкаченного кода нельзя: на хостах окажется смесь, которую нечем объяснить.

---

## 1. Что показал инвентарь прода (и чем это отличается от плана)

Инвентарь снят 25.09 на живом core. Половина страхов плана не подтвердилась, зато нашлось
одно место, где план был бы опасен.

**Опасность планом недооценена — каталог состояния ячеек.** В `/opt/omnia-runtime` лежит
compose-проект `omnia-runtime` (`postgres-compose.yml`, `registry-compose.yml`). Имя проекта
Compose берёт из имени каталога. Переименуем каталог — проект станет `yleum-runtime`, и сеть
`omnia-runtime_default` пересоздастся под новым именем. К этой сети прицеплены
`omnia-prod-worker`, `omnia-postgres-users`, `omnia-registry`, и через неё ходят ячейки.
**Поэтому каталог не переносим.** Вместо переноса `/opt/yleum-runtime` делается симлинком на
`/opt/omnia-runtime`: новое имя работает везде, личность проекта и сеть остаются на месте.
Настоящий перенос — только вместе с переименованием самого стека рантайма и после оборота
ячеек.

**Страхи, не подтвердившиеся на живом проде:**

| Опасение плана | Что на самом деле |
|---|---|
| «Тома нужно подключить к новым контейнерам явно, иначе поднимется пустым» | Тома названы по проекту Compose: `full_postgres-data`, `full_minio-data`, `full_redis-data`. Имя проекта — `full`, от бренда не зависит и не меняется. Переименование контейнеров тома не трогает вообще |
| Сервисы платформы найдут друг друга по новым именам? | Они и не ищут по ним: в живых переменных `api` стоят `redis://redis:6379`, `http://gateway:8001`, `minio:9000` — это **имена сервисов**, а не контейнеров. `container_name` для связи внутри стека не используется |
| Клиентские ячейки адресуют платформу по именам контейнеров | Нет. Проверены переменные **всех** контейнеров на core: ни один не содержит `omnia-prod-*`. Ячейки живут в своих сетях и ходят через прокси |
| nginx проксирует на имена контейнеров | Нет, в vhost'ах таких имён нет |
| Перенос `/opt/omnia` порвёт compose | Каталог никуда не смонтирован (в compose он только контекст сборки), а имя проекта Compose берётся из папки `full`, которая не меняется |

**Что в это окно НЕ входит** — не потому что страшно, а потому что окно тут не помогает.
Это задачи оборота, а не простоя:

- имена ресурсов ячеек `omnia-cell-*` и `omnia-machine-*` — вычисляются кодом
  детерминированно и служат для **поиска**; переименуй их, и оркестратор ослепнет на все
  живые ячейки;
- образы `omnia-template-*`, `omnia-dev-*`, `omnia-app-*` — на них ссылаются работающие
  контейнеры клиентов;
- контейнеры стека рантайма `omnia-buildkitd`, `omnia-registry`, `omnia-postgres-users` и
  сеть `omnia-runtime_default`.

Всё перечисленное переименовывается той же схемой, что и метки: фаза «ищем по обоим именам»,
затем оборот, затем переход. Отдельным шагом 4.4б.

---

## 2. Что делаем в окне

| # | Что | Где | Обратимость |
|---|---|---|---|
| 1 | `container_name: omnia-prod-*` → `yleum-prod-*` (9 контейнеров) | прод-compose | вернуть прежний compose и `up -d` |
| 2 | ключ сети `omnia-prod` → `yleum-prod` (внешняя `omnia-runtime_default` — только ключ, имя остаётся) | прод-compose | то же |
| 3 | умолчания адресов платформы в оркестраторе (`omnia-prod-minio:9000`, `omnia-prod-gw:8001`) | код оркестратора | то же |
| 4 | `/opt/omnia` → `/opt/yleum`, симлинк `/opt/omnia` → `/opt/yleum` остаётся навсегда | core и commerce | `mv` обратно |
| 5 | `/opt/yleum-runtime` — **симлинк** на `/opt/omnia-runtime` | core и commerce | `rm` симлинка |
| 6 | юнит `omnia-orchestrator.service` → `yleum-orchestrator.service` | core и commerce | вернуть старый юнит из бэкапа |
| 7 | умолчания в скриптах резервного копирования и проверки восстановления | `infra/backup` | вернуть коммит |

---

## 3. Перед окном (обязательно)

1. **Волна 4.1–4.3 выкачена и проверена**, `/api/health` 200 и одинаковый release_sha.
2. **Свежая резервная копия и доказательство, что она разворачивается:**
   `infra/backup/backup-omnia.sh`, затем `infra/backup/restore-test-omnia.sh` — он проверяет
   контрольные суммы, восстанавливает оба дампа PostgreSQL в отдельные базы и распаковывает
   архивы во временный каталог; живые базы и тома целями восстановления не становятся.
   Результат пишется в `backups/RESTORE_TEST.json` — в нём должно стоять `"ok": true` и
   сегодняшняя дата.
3. **Зелёный CI** ревизии с правками окна.
4. **Замок выкатки занят** — чтобы никто не начал вторую выкатку посередине.
5. **Согласие сессии откатов**: её прогон занимает до часа и переживёт рестарт платформы
   плохо.
6. **Снимок состояния до окна**: `docker ps -a` и список живых ячеек в файл на core — чтобы
   после окна сверять не по памяти.

**Категорически нельзя** запускать здесь `docker volume prune` или `docker system prune`:
тома остановленных ячеек выглядят как ненужные, и это уже приводило к потере данных.

---

## 4. Порядок выполнения

Всё делается одним заходом, чтобы платформа пересоздавалась один раз, а не четыре.

```bash
# 0. core: занять замок и снять снимок состояния
ssh max-core 'set -o noclobber; echo "rename-4-4 $(date -u +%FT%TZ)" > /opt/omnia/.deploy.lock
              sudo mkdir -p /root/pre-4-4
              docker ps -a --format "{{.Names}} {{.Image}} {{.State}}" | sudo tee /root/pre-4-4/containers.txt >/dev/null
              docker ps -a --filter label=omnia.managed --format "{{.Names}}" | sort | sudo tee /root/pre-4-4/cells.txt >/dev/null
              docker volume ls --format "{{.Name}}" | sudo tee /root/pre-4-4/volumes.txt >/dev/null
              sudo cp /etc/systemd/system/omnia-orchestrator.service /root/pre-4-4/
              sudo cp -r /etc/systemd/system/omnia-orchestrator.service.d /root/pre-4-4/ 2>/dev/null || true
              cd /opt/omnia/apps/llm-gateway/deploy/full && sudo cp docker-compose.yml /root/pre-4-4/
              git -C /opt/omnia rev-parse HEAD | sudo tee /root/pre-4-4/OLD_SHA >/dev/null'

# 1. core: подтянуть ревизию и собрать окружение ДО остановки службы
#    Порядок важен: если рестартовать оркестратор раньше merge, он поднимется со
#    старыми умолчаниями адресов платформы ровно в момент, когда контейнеры меняют
#    имена. Поэтому сначала код, потом остановка.
ssh max-core 'cd /opt/omnia && git fetch -q origin && git merge --ff-only <sha>
              cd apps/orchestrator && ~/.local/bin/uv sync --frozen'

# 2. core: остановить оркестратор
ssh max-core 'sudo systemctl stop omnia-orchestrator'

# 3. core: перенести чекаут, симлинк обратно, ВТОРОЕ имя для каталога состояния
ssh max-core 'sudo mv /opt/omnia /opt/yleum && sudo ln -s /opt/yleum /opt/omnia
              sudo ln -s /opt/omnia-runtime /opt/yleum-runtime
              ls -ld /opt/omnia /opt/yleum /opt/yleum-runtime'

# 4. core: пересобрать venv из НОВОГО пути
#    Внутри окружения 24 скрипта содержат путь жёстко, включая шебанг
#    `#!/opt/omnia/apps/orchestrator/.venv/bin/python`. Без пересборки они работают
#    только через симлинк, то есть симлинк становится несущей конструкцией и его
#    нельзя будет убрать никогда — мина для того, кто однажды приберётся.
ssh max-core 'rm -rf /opt/yleum/apps/orchestrator/.venv
              cd /opt/yleum/apps/orchestrator && ~/.local/bin/uv sync --frozen
              head -1 .venv/bin/uvicorn                      # ждём /opt/yleum/...
              grep -rl "/opt/omnia" .venv/bin | wc -l        # ждём 0'

# 5. core: юнит под новым именем, ВМЕСТЕ с drop-in
#    В drop-in лежит NoNewPrivileges=false — без него служба не стартует.
ssh max-core 'sudo cp /etc/systemd/system/omnia-orchestrator.service /etc/systemd/system/yleum-orchestrator.service
              sudo sed -i "s#/opt/omnia/#/opt/yleum/#g" /etc/systemd/system/yleum-orchestrator.service
              sudo cp -r /etc/systemd/system/omnia-orchestrator.service.d /etc/systemd/system/yleum-orchestrator.service.d
              sudo systemctl disable --now omnia-orchestrator
              sudo rm -rf /etc/systemd/system/omnia-orchestrator.service /etc/systemd/system/omnia-orchestrator.service.d
              sudo systemctl daemon-reload
              sudo systemctl enable --now yleum-orchestrator
              sudo systemctl is-active yleum-orchestrator && curl -s 127.0.0.1:8003/health'

# 6. core: пересоздать стек платформы под новыми именами
ssh max-core 'cd /opt/yleum/apps/llm-gateway/deploy/full && docker compose up -d
              docker ps --format "{{.Names}}" | grep yleum-prod | sort'

# 7. commerce: то же для путей, venv и юнита (стека платформы там нет)
ssh max-core 'ssh commerce "sudo mv /opt/omnia /opt/yleum && sudo ln -s /opt/yleum /opt/omnia
                            sudo ln -s /opt/omnia-runtime /opt/yleum-runtime
                            rm -rf /opt/yleum/apps/orchestrator/.venv
                            cd /opt/yleum/apps/orchestrator && ~/.local/bin/uv sync --frozen
                            sudo cp /etc/systemd/system/omnia-orchestrator.service /etc/systemd/system/yleum-orchestrator.service
                            sudo sed -i \"s#/opt/omnia/#/opt/yleum/#g\" /etc/systemd/system/yleum-orchestrator.service
                            sudo cp -r /etc/systemd/system/omnia-orchestrator.service.d /etc/systemd/system/yleum-orchestrator.service.d 2>/dev/null || true
                            sudo systemctl disable --now omnia-orchestrator
                            sudo rm -rf /etc/systemd/system/omnia-orchestrator.service /etc/systemd/system/omnia-orchestrator.service.d
                            sudo systemctl daemon-reload && sudo systemctl enable --now yleum-orchestrator
                            curl -s 127.0.0.1:8003/health"'
```

Шаг 6 пересоздаёт контейнеры под новыми именами. **`--remove-orphans` намеренно не
используется:** у проекта есть остановленный контейнер отключённого профиля
(`omnia-prod-postgres` с томом `full_postgres-data`, он держится как путь отката базы), и
проверять на проде, сочтёт ли Compose его сиротой, незачем. Старые контейнеры просто
останутся остановленными — это и есть страховка: если что-то пойдёт не так, они поднимаются
прежним compose из `/root/docker-compose.yml.pre-4-4`. Убрать их руками можно после проверки.

---

## 5. Проверка после окна

1. `curl https://yleum.ru/api/health` — 200, одинаковый `orchestrator_release_sha` у обоих.
2. `curl -s 127.0.0.1:8003/health` на core и на commerce — оба живы.
3. `docker ps --format '{{.Names}}'` — девять `yleum-prod-*`, ни одного `omnia-prod-*`.
4. Тома на месте: `docker volume ls | grep full_` — три тома, те же.
5. **Ячейки целы:** список живых ячеек до и после совпадает; открыть одно опубликованное
   приложение и увидеть, что оно отвечает.
6. Очередь превью работает (единственное место, где мог остаться старый путь модуля).
7. Smoke прода зелёный.
8. Резервное копирование: запустить `backup-omnia.sh` вручную и убедиться, что оно находит
   пути по новым именам.

---

## 6. Откат

Откат делается в обратном порядке и целиком, а не по частям:

```bash
ssh max-core 'cd /opt/yleum/apps/llm-gateway/deploy/full && cp /root/pre-4-4/docker-compose.yml docker-compose.yml && docker compose up -d
              sudo systemctl disable --now yleum-orchestrator
              sudo rm -rf /etc/systemd/system/yleum-orchestrator.service /etc/systemd/system/yleum-orchestrator.service.d
              sudo cp /root/pre-4-4/omnia-orchestrator.service /etc/systemd/system/
              sudo cp -r /root/pre-4-4/omnia-orchestrator.service.d /etc/systemd/system/ 2>/dev/null || true
              sudo rm /opt/omnia && sudo mv /opt/yleum /opt/omnia && sudo rm -f /opt/yleum-runtime
              rm -rf /opt/omnia/apps/orchestrator/.venv
              cd /opt/omnia/apps/orchestrator && ~/.local/bin/uv sync --frozen
              sudo systemctl daemon-reload && sudo systemctl enable --now omnia-orchestrator
              curl -s 127.0.0.1:8003/health'
```

Обратите внимание на две строки, без которых откат неполон: **drop-in возвращается вместе
с юнитом** (в нём `NoNewPrivileges=false`, без него служба не стартует), и **окружение
пересобирается из вернувшегося пути** — иначе внутри него останутся ссылки на `/opt/yleum`,
которого больше нет.

Точка невозврата отсутствует: данные не переносятся ни на одном шаге. Единственное, что
нельзя откатить обратной командой, — это удалённые старые контейнеры, но они пересоздаются
из прежнего compose за те же полминуты.
