import { readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * Шрифты для картинок, которые рисуются на сервере: карточка ссылки для
 * мессенджеров и иконка для домашнего экрана iOS.
 *
 * Зачем отдельно от `fonts.css`. Рисовальщик картинок (satori внутри `next/og`)
 * не умеет читать woff2 — только woff, ttf и otf. Поэтому для него лежит
 * отдельный набор статических файлов, а браузеру достаются сжатые переменные
 * из `src/app/fonts`.
 *
 * Зачем вообще. Без явного списка шрифтов `next/og` скачивает свой шрифт из
 * Google ВО ВРЕМЯ СБОРКИ: после переноса остальных шрифтов в репозиторий именно
 * эти три запроса оставались последней ниточкой к чужому сервису, из-за которой
 * сборка на проде падала бы в день его недоступности.
 *
 * Почему два семейства, а не одно. Файлы Google разделены по алфавитам, и
 * склеить их без специальных инструментов нельзя. Рисовальщик умеет подбирать
 * шрифт посимвольно по списку семейств, поэтому кириллица объявлена как
 * отдельное семейство и стоит в списке сразу за латиницей — см. `OG_FONT_FAMILY`.
 *
 * Файлы лежат в `public/`, а не в `src/`: сборка в самостоятельный образ
 * копирует туда `public`, а исходники — нет, и чтение по пути из `src` на проде
 * упало бы с «файл не найден».
 */

const DIR = join(process.cwd(), "public", "fonts", "og");

function read(name: string): Buffer {
  return readFileSync(join(DIR, name));
}

export const OG_FONTS = [
  { name: "Inter", data: read("inter-latin-400.woff"), weight: 400 as const, style: "normal" as const },
  { name: "Inter", data: read("inter-latin-700.woff"), weight: 700 as const, style: "normal" as const },
  { name: "InterCyrillic", data: read("inter-cyrillic-400.woff"), weight: 400 as const, style: "normal" as const },
  { name: "InterCyrillic", data: read("inter-cyrillic-700.woff"), weight: 700 as const, style: "normal" as const },
];

/** Порядок важен: сначала латиница, затем кириллица, затем системный запас. */
export const OG_FONT_FAMILY = "Inter, InterCyrillic, system-ui, sans-serif";
