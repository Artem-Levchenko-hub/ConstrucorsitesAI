/**
 * Парсер контента ассистента: разбивает его на куски прозы и файловые блоки
 * `<file path="...">...</file>`, чтобы UI мог рендерить их по-разному.
 *
 * Зеркалит регексп с бэка (apps/api/src/omnia_api/services/file_extractor.py).
 * Если бэк-парсер обновляется — обновить тут.
 */

export type AppErrorCategory =
  | "build"
  | "compile"
  | "schema"
  | "runtime"
  | "client";

export type AssistantPart =
  | { kind: "text"; text: string }
  | { kind: "file"; path: string; body: string; closed: boolean }
  // Surgical edit (`<edit path="...">` with SEARCH/REPLACE inside). Rendered as
  // a compact "Правка" chip, NOT as raw diff text — otherwise a follow-up edit
  // dumps the whole SEARCH/REPLACE block into the chat.
  | { kind: "edit"; path: string; body: string; closed: boolean }
  // App build/runtime failure (`<app-error category=… title=… file=… fixable=…>`
  // detail `</app-error>`). Mirrors apps/api/src/omnia_api/services/app_errors.py.
  // Rendered as a red error card with an optional "Починить" action.
  | {
      kind: "app-error";
      category: AppErrorCategory;
      title: string;
      file: string | null;
      fixable: boolean;
      body: string;
      closed: boolean;
    }
  // Hot-fork recap (`<remix name=… dna=…>` with one starter-edit per body line).
  // Mirrors apps/api/src/omnia_api/services/fork_recap.py — the warm seed message
  // a remixer lands on. Rendered as a RemixRecapCard, not raw text.
  | {
      kind: "remix";
      name: string;
      dna: string;
      suggestions: string[];
    }
  // One-click installer card (`<install-bundle>`). Owner 2026-06-19 — on a run/
  // install intent the server streams this marker; the UI renders a prominent
  // «Скачать установщик» button (downloads the project .zip, which ships run.bat).
  | { kind: "install" };

// Matches the opening tag of a file / edit / app-error / remix block. The
// attribute string is captured generically (group 2) and parsed per-tag below.
// Mirrors apps/api file_extractor.py (`<file>`/`<edit>`) + app_errors.py + fork_recap.py.
const BLOCK_OPEN = /<(file|edit|app-error|remix|install-bundle)\b([^>]*)>/g;

function getAttr(attrs: string, name: string): string | null {
  const m = attrs.match(new RegExp(`${name}="([^"]*)"`));
  return m ? m[1] : null;
}

function makePart(
  tag: "file" | "edit" | "app-error" | "remix" | "install-bundle",
  attrs: string,
  body: string,
  closed: boolean,
): AssistantPart {
  if (tag === "install-bundle") {
    return { kind: "install" };
  }
  if (tag === "remix") {
    return {
      kind: "remix",
      name: getAttr(attrs, "name") ?? "проект",
      dna: getAttr(attrs, "dna") ?? "",
      // One starter-edit prompt per non-empty body line (see fork_recap.py).
      suggestions: body
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean),
    };
  }
  if (tag === "app-error") {
    return {
      kind: "app-error",
      category: (getAttr(attrs, "category") ?? "build") as AppErrorCategory,
      title: getAttr(attrs, "title") ?? "Ошибка приложения",
      file: getAttr(attrs, "file"),
      fixable: getAttr(attrs, "fixable") !== "0",
      body,
      closed,
    };
  }
  return { kind: tag, path: getAttr(attrs, "path") ?? "", body, closed };
}

// Markdown code fence (```lang\n…```). When the model ignores the <file>
// contract and dumps a fenced code block (owner 2026-06-19: a `code` project
// follow-up returned ```html …``` straight into the chat), we lift it OUT of the
// prose into a collapsed code chip — same treatment as a <file> block — instead
// of rendering a wall of raw code. Mirrors backend salvage (_salvage_html).
const CLOSED_FENCE_RE = /```([a-zA-Z0-9_+#-]*)[ \t]*\r?\n([\s\S]*?)```/g;
const FENCE_EXT: Record<string, string> = {
  html: "html", python: "py", py: "py", javascript: "js", js: "js",
  typescript: "ts", ts: "ts", tsx: "tsx", jsx: "jsx", css: "css",
  json: "json", bash: "sh", sh: "sh", shell: "sh", go: "go", rust: "rs",
  java: "java", kotlin: "kt", php: "php", ruby: "rb", sql: "sql", yaml: "yml",
};
function fenceLabel(lang: string): string {
  if (!lang) return "код";
  return `код · ${FENCE_EXT[lang] ?? lang}`;
}

/** Split a prose chunk into text + fenced-code parts. A ```fence``` becomes a
 *  collapsed `file` chip (closed), an unterminated trailing fence (mid-stream)
 *  becomes an open one. Plain prose passes through untouched. */
function expandFences(text: string): AssistantPart[] {
  const out: AssistantPart[] = [];
  let cursor = 0;
  CLOSED_FENCE_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = CLOSED_FENCE_RE.exec(text)) !== null) {
    const pre = text.slice(cursor, m.index);
    if (pre.trim()) out.push({ kind: "text", text: pre });
    out.push({
      kind: "file",
      path: fenceLabel((m[1] || "").toLowerCase()),
      body: m[2].replace(/\s+$/, ""),
      closed: true,
    });
    cursor = m.index + m[0].length;
  }
  let tail = text.slice(cursor);
  const openIdx = tail.indexOf("```");
  if (openIdx !== -1) {
    const om = tail.slice(openIdx).match(/^```([a-zA-Z0-9_+#-]*)[ \t]*\r?\n?([\s\S]*)$/);
    if (om) {
      const pre = tail.slice(0, openIdx);
      if (pre.trim()) out.push({ kind: "text", text: pre });
      out.push({
        kind: "file",
        path: fenceLabel((om[1] || "").toLowerCase()),
        body: om[2],
        closed: false,
      });
      tail = "";
    }
  }
  if (tail.trim()) out.push({ kind: "text", text: tail });
  return out;
}

/**
 * Делит content на части в порядке появления. `<file>` / `<edit>` / `<app-error>`
 * блоки выносятся в свои части (UI рисует их как чипы / карточки, а не сырой
 * текст). Незакрытый блок в конце (типично во время стриминга) возвращается с
 * `closed: false`. Прозовые куски дополнительно прогоняются через
 * ``expandFences`` — ```code``` фенсы тоже становятся чипами, а не стеной текста.
 */
export function parseAssistantContent(content: string): AssistantPart[] {
  if (!content) return [];

  const parts: AssistantPart[] = [];
  let cursor = 0;

  BLOCK_OPEN.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = BLOCK_OPEN.exec(content)) !== null) {
    const tag = match[1] as
      | "file"
      | "edit"
      | "app-error"
      | "remix"
      | "install-bundle";
    const attrs = match[2];
    const openStart = match.index;
    const openEnd = openStart + match[0].length;

    if (openStart > cursor) {
      const text = content.slice(cursor, openStart);
      if (text.trim()) parts.push(...expandFences(text));
    }

    const closeTag = `</${tag}>`;
    const closeIdx = content.indexOf(closeTag, openEnd);
    if (closeIdx === -1) {
      parts.push(makePart(tag, attrs, content.slice(openEnd), false));
      cursor = content.length;
      break;
    }

    parts.push(makePart(tag, attrs, content.slice(openEnd, closeIdx), true));
    cursor = closeIdx + closeTag.length;
    BLOCK_OPEN.lastIndex = cursor;
  }

  if (cursor < content.length) {
    const tail = content.slice(cursor);
    if (tail.trim()) parts.push(...expandFences(tail));
  }

  return parts;
}

const FENCE_RE = /```[\s\S]*?```/g;
const HTML_DOCISH_RE =
  /<!doctype html|<html[ >]|<\/(?:section|div|main|header|footer|body|article|nav)>/i;

/**
 * Strip leaked code from an assistant PROSE chunk so the chat never renders raw
 * HTML/code when a cheap model replied conversationally (```html / bare HTML)
 * instead of with an `<edit>`/`<file>` block. Returns clean human prose, or ""
 * when the whole chunk was a code dump. Belt-and-suspenders to the server-side
 * `clean_chat_content` (apps/api file_extractor.py) — update both together.
 */
export function cleanChatProse(text: string): string {
  const noFence = text.replace(FENCE_RE, "");
  const tags = (noFence.match(/</g) ?? []).length;
  if (HTML_DOCISH_RE.test(noFence) || (tags >= 3 && tags * 40 >= noFence.length)) {
    return "";
  }
  return noFence.trim();
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
