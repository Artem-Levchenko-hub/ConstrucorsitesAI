# Очистка production-сервера — 9 сентября 2026

Автор: GPT-6 Astra. Запрос владельца: полноценная очистка накопленного мусора.

## Статус: выполнена безопасная системная часть, Docker-очистка не выполнена

Освобождено **3 761 942 528 байт (3,50 ГиБ)** по разнице свободного места непосредственно до/после. Диск всё ещё заполнен на **95%**; полная очистка не заявляется.

| Показатель | До | После |
|---|---:|---:|
| Свободное место | 25 257 684 992 байт / 23,52 ГиБ | 29 019 627 520 байт / 27,03 ГиБ |
| Занятость файловой системы | 96% | 95% |
| APT archives | 764 715 008 байт | 90 112 байт |
| Системный journal | 3 804 004 352 байт | 931 962 880 байт |
| Кэш скачанных .deb | 1053 файла | 0 файлов |
| Контейнеры / работающие / тома | 65 / 33 / 281 | 65 / 33 / 281 |

Общий эффект включает дополнительные индексы кэша APT. После операции весь /var/cache/apt занимает 94 208 байт. Размеры live-файлов могут изменяться во время дальнейшей работы; цифры относятся к этому замеру.

## Что удалено

1. Скачанные пакеты и восстанавливаемые индексы APT. Установленные пакеты не удалялись и не обновлялись.
2. 78 архивных файлов systemd journal старше 14 дней, включая старые архивы с суффиксом journal~. Активный журнал не обнулялся, принудительная ротация не запускалась.

Перед операцией проверены точные resolved paths /var/cache/apt/archives и /var/log/journal, отсутствие занятого package-manager lock; сняты размеры, контейнеры, тома и время запуска процессов.

Выполненные команды:

~~~sh
sudo -n apt-get -o Dir::Cache=/var/cache/apt -o Dir::Cache::archives=archives clean
sudo -n journalctl --vacuum-time=14d
~~~

Кэш можно создать повторно загрузкой пакетов. Удалённые старые журналы не имеют созданной в этой задаче резервной копии и не могут быть восстановлены этой очисткой. Последние 14 дней и активные журналы сохранялись по штатным правилам journalctl.

## Что сохранено

Все контейнеры (в том числе остановленные), Docker images, build cache, 281 volume, базы PostgreSQL/Redis, MinIO, snapshots/версии, исходники проектов, резервные копии, временные release/restore-артефакты и unrelated server Git-изменения.

Не выполнялись Docker prune/rm, удаление runtime-каталогов, остановка/перезапуск сервисов, генерация приложения, миграции, восстановление поверх live-данных или запуск restore-test в production DB.

До и после совпали SHA256 списков всех container IDs и volume names, а также пары running-container ID / StartedAt. Это подтверждает сохранение объектов и отсутствие перезапусков от очистки, а не проверку содержимого всех пользовательских строк БД.

## Основной объём и ограничения продолжения

Docker system df на момент обследования: images 276 GB (473 image objects, 29 active), build cache 91,57 GB (588 records), volumes 26,28 GB. Метрики shared/reclaimable не складываются в гарантированный объём будущего освобождения.

Работает один builder default с docker driver, общий для нескольких приложений. Согласно docs/08-vps-setup.md, шаг 0, и разделу 13 Project Cell design, глобальные system/image/builder prune и любое volume prune запрещены. Для Docker-удалений требуются адресный reviewed manifest, проверка ссылок из releases/snapshots/config, доказанное восстановление и maintenance gate. Эти условия в данной задаче не закрыты. Ни один Docker-объект не удалён.

Свежий encrypted off-host backup от 2026-09-09T00:15:50Z: 415 878 851 байт, SHA256 1275e86c101bcbd2c0b9245b5245d4699e9f4319a5bef9bef1a9474f74afbbc8. CI 34332711275 success проверил размер, checksum и CMS envelope и сохранил копию вне сервера. Это **не** свежий restore-test: найденные local restore-test logs датированы 31 июля. Сам тест в production не запускался.

Штатный backup script явно сохраняет legacy project sources, shared project DB и MinIO. Прямое покрытие актуальных live PostgreSQL/workspace volumes новых Project Cell и успешное полное восстановление этих ячеек этой проверкой не подтверждены. Наличие snapshots в MinIO не является само по себе доказательством такого восстановления. Резервные копии и все эти тома сохранены.

Следующий этап требует решения владельца о допустимой адресной очистке общего builder cache и закрытия соответствующих recovery/maintenance требований. Удаление старых образов платформы/пользовательских версий — отдельный список после проверки всех ссылок и пути восстановления. Не превращать запрос «убрать мусор» в разрешение удалить бизнес-данные или историю версий.

## Проверки после очистки

- API /api/health: status ok; generation_worker, database, redis, worker, deploy_control_plane и preview_storage — ok.
- Web /web-health: status ok, release c03dd088cda510174e485daa48328436614a6182.
- Host orchestrator active; /health status ok, release b99af4716305d1ff814e6f7b643f11834714f8ed.
- Существующий MAX canary /api/health: status ok, platform max-miniapp.
- Локальный BASE отчёта: c03dd088, upstream origin/main, clean, 0/0 после fetch.
- Server checkout при начале обследования: b99af471, на один web-коммит позади upstream; web runtime уже c03dd088. Пять secondbrain tracked edits сохранены. Поставка этого отчёта не требует runtime restart.

Сведения о свежести и health необходимо перепроверять перед следующим этапом.

## Первичные ссылки

- [Off-host backup CI](https://github.com/Artem-Levchenko-hub/ConstrucorsitesAI/actions/runs/34332711275)
- [Docker: адресные фильтры Buildx cache](https://docs.docker.com/reference/cli/docker/buildx/prune/)
- [Docker: reclaimable и shared cache](https://docs.docker.com/reference/cli/docker/buildx/du/)
