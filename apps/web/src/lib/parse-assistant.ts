/**
 * Парсер контента ассистента: разбивает его на куски прозы и файловые блоки
 * `<file path="...">...</file>`, чтобы UI мог рендерить их по-разному.
 *
 * Зеркалит регексп с бэка (apps/api/src/omnia_api/services/file_extractor.py).
 * Если бэк-парсер обновляется — обновить тут.
 */

export type AssistantPart =
  | { kind: "text"; text: string }
  | { kind: "file"; path: string; body: string; closed: boolean }
  // Surgical edit (`<edit path="...">` with SEARCH/REPLACE inside). Rendered as
  // a compact "Правка" chip, NOT as raw diff text — otherwise a follow-up edit
  // dumps the whole SEARCH/REPLACE block into the chat.
  | { kind: "edit"; path: string; body: string; closed: boolean };

// Matches the opening tag of either an AI file block or a surgical edit block.
// Mirrors apps/api/src/omnia_api/services/file_extractor.py (`<file>`/`<edit>`).
const BLOCK_OPEN = /<(file|edit)\s+path="([^"]+)"\s*>/g;

/**
 * Делит content на части в порядке появления. `<file>` и `<edit>` блоки
 * выносятся в свои части (UI рисует их как чипы, а не сырой текст). Незакрытый
 * блок в конце (типично во время стриминга) возвращается с `closed: false`.
 */
export function parseAssistantContent(content: string): AssistantPart[] {
  if (!content) return [];

  const parts: AssistantPart[] = [];
  let cursor = 0;

  BLOCK_OPEN.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = BLOCK_OPEN.exec(content)) !== null) {
    const tag = match[1] as "file" | "edit";
    const openStart = match.index;
    const openEnd = openStart + match[0].length;
    const path = match[2];

    if (openStart > cursor) {
      const text = content.slice(cursor, openStart);
      if (text.trim()) parts.push({ kind: "text", text });
    }

    const closeTag = `</${tag}>`;
    const closeIdx = content.indexOf(closeTag, openEnd);
    if (closeIdx === -1) {
      parts.push({ kind: tag, path, body: content.slice(openEnd), closed: false });
      cursor = content.length;
      break;
    }

    parts.push({
      kind: tag,
      path,
      body: content.slice(openEnd, closeIdx),
      closed: true,
    });
    cursor = closeIdx + closeTag.length;
    BLOCK_OPEN.lastIndex = cursor;
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
 * Как collectStreamingFiles, но включает и не закрытые `<file>` блоки
 * (с body до текущего конца стрима). Для realtime-preview, где нужно показать
 * частично написанный index.html, не дожидаясь `</file>`.
 */
export function collectStreamingFilesPartial(
  content: string,
): Record<string, string> {
  const files: Record<string, string> = {};
  for (const p of parseAssistantContent(content)) {
    if (p.kind === "file") files[p.path] = p.body;
  }
  return files;
}

/**
 * Возвращает body последнего встретившегося `<file path="index.html">` блока
 * (открытого или закрытого) — или null, если index.html ещё не начался.
 */
export function extractStreamingBody(content: string): string | null {
  let last: string | null = null;
  for (const p of parseAssistantContent(content)) {
    if (p.kind === "file" && p.path === "index.html") last = p.body;
  }
  return last;
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
