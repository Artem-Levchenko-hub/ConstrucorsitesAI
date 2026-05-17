/**
 * Парсер контента ассистента: разбивает его на куски прозы и файловые блоки
 * `<file path="...">...</file>`, чтобы UI мог рендерить их по-разному.
 *
 * Зеркалит регексп с бэка (apps/api/src/omnia_api/services/file_extractor.py).
 * Если бэк-парсер обновляется — обновить тут.
 */

export type AssistantPart =
  | { kind: "text"; text: string }
  | { kind: "file"; path: string; body: string; closed: boolean };

const FILE_OPEN = /<file\s+path="([^"]+)"\s*>/g;

/**
 * Делит content на части в порядке появления. Незакрытый `<file>` в конце
 * (типично во время стриминга, пока модель не дописала `</file>`) тоже
 * возвращается, но с `closed: false` — UI может показать его как «генерируется».
 */
export function parseAssistantContent(content: string): AssistantPart[] {
  if (!content) return [];

  const parts: AssistantPart[] = [];
  let cursor = 0;

  FILE_OPEN.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = FILE_OPEN.exec(content)) !== null) {
    const openStart = match.index;
    const openEnd = openStart + match[0].length;
    const path = match[1];

    if (openStart > cursor) {
      const text = content.slice(cursor, openStart);
      if (text.trim()) parts.push({ kind: "text", text });
    }

    const closeIdx = content.indexOf("</file>", openEnd);
    if (closeIdx === -1) {
      parts.push({
        kind: "file",
        path,
        body: content.slice(openEnd),
        closed: false,
      });
      cursor = content.length;
      break;
    }

    parts.push({
      kind: "file",
      path,
      body: content.slice(openEnd, closeIdx),
      closed: true,
    });
    cursor = closeIdx + "</file>".length;
    FILE_OPEN.lastIndex = cursor;
  }

  if (cursor < content.length) {
    const tail = content.slice(cursor);
    if (tail.trim()) parts.push({ kind: "text", text: tail });
  }

  return parts;
}

/** dict путь→тело по всем `<file>`-блокам (только закрытым). */
export function collectStreamingFiles(content: string): Record<string, string> {
  const files: Record<string, string> = {};
  for (const p of parseAssistantContent(content)) {
    if (p.kind === "file" && p.closed) files[p.path] = p.body;
  }
  return files;
}

/**
 * Собирает HTML, пригодный для iframe srcDoc во время стриминга: inline-ит
 * относительные `<link rel="stylesheet" href="style.css">` и
 * `<script src="...">` из dict-а сгенерированных файлов. Без этого srcDoc
 * iframe не найдёт CSS/JS — он изолирован и не ходит на сетку проекта.
 *
 * Возвращает null, если index.html ещё не сгенерирован.
 */
export function buildStreamingPreview(content: string): string | null {
  const files = collectStreamingFiles(content);
  const html = files["index.html"];
  if (!html) return null;
  return inlineAssets(html, files);
}

function inlineAssets(html: string, files: Record<string, string>): string {
  return html
    .replace(
      /<link\s+[^>]*?rel=["']stylesheet["'][^>]*?href=["']([^"']+)["'][^>]*?\/?>/gi,
      (m, href) =>
        files[href] ? `<style>\n${files[href]}\n</style>` : m,
    )
    .replace(
      /<link\s+[^>]*?href=["']([^"']+)["'][^>]*?rel=["']stylesheet["'][^>]*?\/?>/gi,
      (m, href) =>
        files[href] ? `<style>\n${files[href]}\n</style>` : m,
    )
    .replace(
      /<script\s+[^>]*?src=["']([^"']+)["'][^>]*?>\s*<\/script>/gi,
      (m, src) =>
        files[src] ? `<script>\n${files[src]}\n</script>` : m,
    );
}

/** Размер в байтах → «1.2 KB». */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
