#!/usr/bin/env bash
# Проверка того, что ночная копия действительно содержит платформу.
#
# Отдельный файл, а не кусок backup-omnia.sh, ровно по одной причине: это
# единственная часть ночного задания, которую можно прогнать на подделанных
# входных данных, и её надо держать проверяемой.
#
# Главное здесь — различать две беды, которые легко спутать:
#
#   «данных нет»          — архив читается, но в нём нет того, что должно быть.
#   «проверка сломалась»  — архив не читается вовсе, и о содержимом ничего не
#                           известно.
#
# Их путали, потому что счётчики писались как `... || true`: нечитаемый архив
# давал ноль совпадений, и отчёт винил данные. Для оператора это противоположные
# указания: в первом случае копия снята зря, во втором копия испорчена и её
# нельзя использовать для восстановления. Ошибиться здесь значит либо
# восстанавливаться из мусора, либо зря перезапускать исправное задание.
#
#   verify-bundle.sh <каталог-копии> <число-проектов-в-базе>
#
# Коды выхода: 0 — всё на месте; 3 — данных не хватает; 4 — архив нечитаем.

set -euo pipefail

EXIT_MISSING_DATA=3
EXIT_UNREADABLE=4

die() { printf '%s\n' "$2" >&2; exit "$1"; }

dir="${1:?каталог копии не указан}"
live_projects="${2:-unknown}"
platform_dump="${PLATFORM_DUMP:-${dir}/platform-omnia.sql.gz}"
minio_archive="${MINIO_ARCHIVE:-${dir}/minio-data.tgz}"

# 1. Дамп платформы обязан читаться. Нечитаемый gzip — это испорченная копия, а
#    не пустая база, и путать их нельзя.
if ! gzip -t "$platform_dump" 2>/dev/null; then
  die "$EXIT_UNREADABLE" "unreadable: ${platform_dump} is not a readable gzip stream"
fi

for table in users projects snapshots; do
  # Поток читается целиком: ранний выход grep под pipefail превратил бы SIGPIPE
  # в ложный отказ — ровно та ошибка, что уже случалась. И распаковка вызывается
  # как gzip -dc, а не zcat: на macOS zcat ищет .Z и проверку нельзя прогнать
  # на машине разработчика.
  hits="$(gzip -dc "$platform_dump" | grep -c "^CREATE TABLE public\.${table} " || true)"
  if [ "${hits:-0}" -lt 1 ]; then
    die "$EXIT_MISSING_DATA" "missing: platform dump has no ${table} table"
  fi
done

# 2. То же для архива MinIO: сначала читаемость, потом содержимое.
if ! tar -tzf "$minio_archive" >/dev/null 2>&1; then
  die "$EXIT_UNREADABLE" "unreadable: ${minio_archive} is not a readable tar.gz"
fi

archived_repos="$(tar -tzf "$minio_archive" \
  | grep -c -E '^\./projects/repos/[0-9a-f-]{36}\.tar\.gz/xl\.meta$' || true)"

case "$live_projects" in
  ''|unknown)
    # Сверка не состоялась. «Проверено» об этом говорить нельзя: непроведённая
    # проверка, названная успешной, ровно так и создаёт ложную уверенность.
    printf 'warning: live project count unknown; repo count not compared (%s archived)\n' \
      "$archived_repos"
    exit 0
    ;;
  0) : ;;
  *)
    if [ "$archived_repos" -lt "$live_projects" ]; then
      die "$EXIT_MISSING_DATA" \
        "missing: MinIO archive holds ${archived_repos} project repos but the platform has ${live_projects}"
    fi
    ;;
esac

printf 'content check OK: %s project repos archived for %s live projects\n' \
  "$archived_repos" "$live_projects"
