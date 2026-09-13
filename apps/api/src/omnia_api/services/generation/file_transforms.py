from __future__ import annotations

import re

from omnia_api.services.file_extractor import (
    apply_edits,
    extract_edits,
    extract_files,
)
from omnia_api.services.vendor_profiles import vendor_directive

_EMPTY_RESPONSE_FALLBACKS: dict[str, list[str]] = {
    "gemini-2.5-pro": ["gemini-2.5-flash", "claude-haiku-4-5", "gpt-5-nano"],
    "gemini-2.5-flash": ["claude-haiku-4-5", "gpt-5-nano"],
    # proxyapi.ru occasionally short-replies even for Haiku (we've seen
    # acc_len=3 / tokens_out=2 on long prompts). Fallback to gpt-5-nano on
    # the same proxyapi balance — different upstream, same key, same money.
    "claude-haiku-4-5": ["gpt-5-nano", "gigachat-2-pro"],
    "claude-sonnet-4-6": ["claude-haiku-4-5", "gpt-5-nano"],
    "claude-opus-4-7": ["claude-sonnet-4-6", "claude-haiku-4-5"],
    # DeepSeek (vsegpt) is the worker-role default now. A vsegpt hiccup, or an
    # over-long chain-of-thought that truncates the visible answer, degrades to
    # the reliable proxyapi route (Haiku → Sonnet) instead of an empty preview.
    "deepseek-chat": ["claude-haiku-4-5", "claude-sonnet-4-6"],
    "deepseek-v4-flash-thinking": ["claude-haiku-4-5", "claude-sonnet-4-6"],
    # The freeform WRITER (`deepseek-v4-pro`) and the route model
    # (`deepseek-v4-pro-thinking`) can shadow-drop the whole page — 0 chars, the
    # reasoning field eats the token budget and the visible answer is empty.
    # Neither had a fallback, so an empty writer shipped a BLANK build (owner
    # trace 2026-06-03, msg 9c83ada4: writer_raw chars=0 → same-model multipass
    # retry → `non-JSON content` → dead). Switch to a DIFFERENT live model: Kimi
    # (the art_director brain — already loaded every build, writes HTML well),
    # then the cheap deepseek-chat. Stays on vsegpt (proxyapi is gone).
    "deepseek-v4-pro": ["kimi-k2.6-thinking", "deepseek-chat"],
    "deepseek-v4-pro-thinking": ["kimi-k2.6-thinking", "deepseek-chat"],
    # GPT-5 family are reasoning models and may shadow-drop output even with
    # reasoning_effort=minimal; fall back to Haiku (same proxyapi key).
    "gpt-5": ["claude-haiku-4-5", "gpt-5-nano"],
    "gpt-5-nano": ["claude-haiku-4-5", "gigachat-2-pro"],
    "gpt-5-mini": ["claude-haiku-4-5", "gpt-5-nano"],
    "gpt-4.1": ["claude-haiku-4-5", "gpt-5"],
}


def _looks_truncated(accumulated: str, files: dict[str, str]) -> bool:
    """Heuristic: did the upstream return usable content?

    True when files extracted = 0 AND the raw text is either empty, very
    short, or trailed off mid-syntax (`<` / ` ```html\\n<` / unfinished tag).
    We deliberately don't try to be too clever: better to occasionally retry
    a perfectly fine refusal than to leave the user staring at an empty
    preview.
    """
    if files:
        return False
    stripped = accumulated.strip()
    if not stripped:
        return True
    if len(stripped) < 50:
        return True
    # Common patterns we've actually seen Gemini cut off with.
    tail = stripped[-10:]
    if tail.endswith("<") or tail.endswith("```html\n<") or tail.endswith("```html"):
        return True
    return False


_EDIT_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9]{3,}")


_EDIT_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.S | re.I)


_EDIT_TAG_RE = re.compile(r"<[^>]+>")


def _visible_words(html: str) -> set[str]:
    """Lowercased set of visible words (≥3 chars) in HTML — tags + script/style
    bodies stripped. Measures how much of a page's CONTENT survived a fallback
    rewrite so a scoped edit (text preserved) is told apart from a silent
    re-design (text replaced)."""
    no_code = _EDIT_SCRIPT_STYLE_RE.sub(" ", html)
    text = _EDIT_TAG_RE.sub(" ", no_code)
    return {w.lower() for w in _EDIT_WORD_RE.findall(text)}


def _text_preserved_ratio(old_html: str, new_html: str) -> float:
    """Fraction of the OLD page's visible words still present in the NEW page.
    ~1.0 = same content (a scoped edit — e.g. only the background changed);
    low = the model rewrote the copy (a re-design we must NOT silently ship)."""
    old = _visible_words(old_html)
    if not old:
        return 1.0
    return len(old & _visible_words(new_html)) / len(old)


_HTML_START_RE = re.compile(r"<!doctype html|<html[ >]", re.I)


_HTML_FENCE_RE = re.compile(r"```(?:html)?\s*(.*?)```", re.S | re.I)


def _salvage_html(text: str) -> str | None:
    """Pull a full HTML document out of a rewrite that forgot the <file> wrapper
    (or fenced it in ```html). The rewrite model sometimes streams raw HTML; this
    rescues it instead of dropping the whole edit. Returns the page or None."""
    t = text.strip()
    fence = _HTML_FENCE_RE.search(t)
    if fence and "<" in fence.group(1):
        t = fence.group(1).strip()
    m = _HTML_START_RE.search(t)
    if not m:
        return None
    html = t[m.start() :]
    end = html.lower().rfind("</html>")
    if end != -1:
        html = html[: end + len("</html>")]
    return html if len(html) > 800 else None


_KIT_LINK = '<link rel="stylesheet" href="assets/omnia-kit.css">'


_ANIME_SCRIPT = '<script src="assets/anime.min.js" defer></script>'


_KIT_SCRIPT = '<script src="assets/omnia-kit.js" defer></script>'


def _merge_seeded_agent_files(
    seeded_files: dict[str, str], generated_files: dict[str, str]
) -> dict[str, str]:
    """Keep the complete verified MAX base while preferring AI customisations.

    The starter is hot-reloaded before the native agent runs. The agent result
    only contains paths it actually wrote, so committing that result alone would
    lose untouched platform files on a brand-new project.
    """

    return {**seeded_files, **generated_files}


_AUTH_USERS_COLUMNS = (
    '  passwordHash: text("password_hash"),\n  role: text("role").notNull().default("user"),\n'
)


_AUTH_TABLES_BLOCK = """
// ─── Auth tables (re-injected by Omnia — the model dropped them) ───────────
export const users = pgTable("users", {
  id: uuid("id").primaryKey().defaultRandom(),
  name: text("name"),
  email: text("email").notNull().unique(),
  emailVerified: timestamp("email_verified", { withTimezone: true }),
  image: text("image"),
  passwordHash: text("password_hash"),
  role: text("role").notNull().default("user"),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().default(sql`now()`),
});

export const accounts = pgTable("accounts", {
  userId: uuid("user_id").notNull().references(() => users.id, { onDelete: "cascade" }),
  type: text("type").$type<AdapterAccountType>().notNull(),
  provider: text("provider").notNull(),
  providerAccountId: text("provider_account_id").notNull(),
  refresh_token: text("refresh_token"),
  access_token: text("access_token"),
  expires_at: integer("expires_at"),
  token_type: text("token_type"),
  scope: text("scope"),
  id_token: text("id_token"),
  session_state: text("session_state"),
}, (account) => ({
  pk: primaryKey({ columns: [account.provider, account.providerAccountId] }),
}));

export const sessions = pgTable("sessions", {
  sessionToken: text("session_token").primaryKey(),
  userId: uuid("user_id").notNull().references(() => users.id, { onDelete: "cascade" }),
  expires: timestamp("expires", { withTimezone: true }).notNull(),
});

export const verificationTokens = pgTable("verification_tokens", {
  identifier: text("identifier").notNull(),
  token: text("token").notNull(),
  expires: timestamp("expires", { withTimezone: true }).notNull(),
}, (vt) => ({
  pk: primaryKey({ columns: [vt.identifier, vt.token] }),
}));

"""


_AUTH_PGCORE_IMPORTS = ("integer", "pgTable", "primaryKey", "text", "timestamp", "uuid")


def _ensure_named_imports(src: str, module: str, needed: tuple[str, ...]) -> str:
    """Ensure `src` imports every name in `needed` from `module`, merging into an
    existing `import { ... } from "module"` line (no duplicate-identifier errors)
    or prepending a fresh one."""
    import re

    pat = re.compile(r'import\s+\{([^}]*)\}\s+from\s+"' + re.escape(module) + r'"\s*;')
    m = pat.search(src)
    if m:
        existing = {x.strip() for x in m.group(1).split(",") if x.strip()}
        merged = sorted(existing | set(needed))
        line = "import { " + ", ".join(merged) + ' } from "' + module + '";'
        return src[: m.start()] + line + src[m.end() :]
    line = "import { " + ", ".join(sorted(needed)) + ' } from "' + module + '";\n'
    return line + src


def _inject_auth_tables(src: str) -> str:
    """Re-inject the four Auth.js tables when the model dropped them entirely.

    Merges the required imports (drizzle-orm/pg-core names, `sql`, and the
    `AdapterAccountType` type) then inserts the canonical table block before the
    model's first `export const`. Fail-soft: on any surprise, returns `src`."""
    try:
        out = _ensure_named_imports(src, "drizzle-orm/pg-core", _AUTH_PGCORE_IMPORTS)
        out = _ensure_named_imports(out, "drizzle-orm", ("sql",))
        if "AdapterAccountType" not in out:
            out = 'import type { AdapterAccountType } from "next-auth/adapters";\n' + out
        idx = out.find("\nexport const ")
        if idx == -1:
            idx = len(out)
        out = out[:idx] + "\n" + _AUTH_TABLES_BLOCK + out[idx:]
        print(
            "[PP] auth_schema_guard: re-injected dropped users/accounts/sessions/"
            "verificationTokens tables",
            flush=True,
        )
        return out
    except Exception as exc:
        print(f"[PP] auth_schema_guard: inject failed {exc!r}", flush=True)
        return src


def _preserve_auth_schema(files: dict[str, str]) -> dict[str, str]:
    """Keep the rewritten Drizzle schema's auth surface intact.

    Two failure modes the model causes when it rewrites ``src/lib/db/schema.ts``
    to add its own tables:
      1. Drops the whole ``users``/``accounts``/``sessions``/``verificationTokens``
         block — ``auth.ts`` imports them by name, so the app 500s on compile.
      2. Keeps ``users`` but strips the ``password_hash``/``role`` columns the
         Credentials provider depends on — signup/login then fail at runtime.

    This guard repairs both. Idempotent + fail-soft: on any parse surprise it
    returns ``files`` as-is.
    """
    path = "src/lib/db/schema.ts"
    src = files.get(path)
    if not src:
        return files
    if 'pgTable("users"' not in src:
        # Mode 1: the entire auth-tables block is gone — re-inject all four.
        return {**files, path: _inject_auth_tables(src)}
    if "password_hash" in src:
        return files
    import re

    m = re.search(r'export const users\s*=\s*pgTable\(\s*"users"\s*,\s*\{', src)
    if not m:
        return files
    # Walk braces from just after the opening `{` to find this object's close,
    # tolerating nested `{ ... }` (e.g. timestamp("x", { withTimezone: true })).
    depth, i, n = 1, m.end(), len(src)
    while i < n and depth > 0:
        ch = src[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        i += 1
    if depth != 0:
        return files
    close_brace = i - 1  # index of the object's closing `}`
    patched = src[:close_brace] + _AUTH_USERS_COLUMNS + src[close_brace:]
    print("[PP] auth_schema_guard: re-injected users.passwordHash/role", flush=True)
    return {**files, path: patched}


def _normalize_entity_filenames(files: dict[str, str]) -> dict[str, str]:
    """Align each ``entities/<X>.json`` filename with its declared ``name``.

    The entity registry resolves a definition STRICTLY by ``entities/<Name>.json``
    (``registry.ts`` ``entityPath`` → ``${name}.json``; ``listEntities`` keys by
    filename), and every consumer references an entity by its ``name`` — the SDK
    (``entities.Client``), reference fields (``"entity": "Client"``), and the
    writer's screens (``<CrudResource entity="Client">``). The contract is
    "filename === Name" (SYSTEM_PROMPT.md, and the starter ``Task.json``).

    But the art-director brief sometimes lists entity files in lowercase / plural
    form (``clients.json``) and the writer copies that filename VERBATIM
    (art_director_writer.py:565 «заведи каждый entities/<Имя>.json ДОСЛОВНО»). The
    result: ``entities/clients.json`` declares ``{"name": "Client"}`` while the
    runtime stats ``entities/Client.json`` → ENOENT → ``loadEntity`` returns null
    → every read/write 404s («unknown entity 'Client'») → the whole app is dead
    (empty lists everywhere, no record can be created), even though the brief,
    the screens and the references are all internally consistent.

    This guard renames such files to match their internal ``name``. Deterministic,
    idempotent, fail-soft: only touches paths under ``entities/`` whose JSON parses
    and carries a valid identifier ``name`` that differs from the filename stem;
    never clobbers a file already named correctly; a no-op for already-correct
    apps and for any stack without ``entities/*.json``.
    """
    import json as _json
    import re as _re

    ident = _re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
    prefix, suffix = "entities/", ".json"

    def stem_of(p: str) -> str:
        return p[len(prefix) : -len(suffix)]

    # Stems already present — never overwrite a correctly-named sibling.
    existing = {stem_of(p) for p in files if p.startswith(prefix) and p.endswith(suffix)}
    out = dict(files)
    renamed: list[str] = []
    for path, content in list(files.items()):
        if not (path.startswith(prefix) and path.endswith(suffix)):
            continue
        stem = stem_of(path)
        try:
            name = _json.loads(content).get("name")
        except Exception:
            continue  # malformed JSON — leave it for the registry/normalize() path
        if not isinstance(name, str) or not ident.match(name) or name == stem:
            continue
        if name in existing:
            continue  # a file already declares this Name — don't clobber it
        out.pop(path, None)
        out[f"{prefix}{name}{suffix}"] = content
        existing.discard(stem)
        existing.add(name)
        renamed.append(f"{stem}->{name}")
    if renamed:
        print(f"[PP] entity_filename_guard: {', '.join(renamed)}", flush=True)
    return out


def _warn_unparseable_entity_json(files: dict[str, str]) -> list[str]:
    """Observability for BS-31: the writer sometimes emits INVALID JSON for an
    entity file (an unterminated string, a dropped key name, …). Today such a
    file passes every guard untouched — `_normalize_entity_filenames` skips
    unparseable files (it cannot read their ``name``) and the runtime
    ``loadEntity`` then silently returns ``null`` (registry.ts:87-89), so
    ``GET /api/entities/<X>`` answers 404 ``unknown entity`` and a declared
    entity is DOA with ZERO signal anywhere. On real CRM builds this hit the
    CENTRAL entity (``Client``) on 2/2 consecutive live gens.

    This does NOT repair, regenerate, or drop the file — that action is
    policy-adjacent and cross-surface (see PROPOSAL P-ENTITYJSON / rule 15). It
    only makes the corruption loud in the build log, at the moment it is
    introduced, so the dominant failure mode is diagnosable instead of silent.
    Returns the list of entity names whose JSON does not parse.
    """
    import json as _json

    bad: list[str] = []
    for path, content in files.items():
        if not (path.startswith("entities/") and path.endswith(".json")):
            continue
        try:
            _json.loads(content)
        except Exception as exc:
            name = path[len("entities/") : -len(".json")]
            bad.append(name)
            print(
                f"[PP] entity_json_INVALID: {name}.json does not parse ({exc}); "
                f"entity will resolve as 404 'unknown entity' at runtime — DOA",
                flush=True,
            )
    return bad


def _extract_files_and_edits(
    accumulated: str, base_files: dict[str, str]
) -> tuple[dict[str, str], list[str]]:
    """Объединяет два формата ответа AI:

    * ``<file path="...">`` — полное содержимое (новый файл / полный rewrite).
    * ``<edit path="...">`` с SEARCH/REPLACE-блоками — точечные правки. Намного
      дешевле по токенам: модель отдаёт ~200-500 символов diff вместо 25K
      переписанного файла.

    Контракт мерджа: если модель прислала и ``<file>``, и ``<edit>`` для
    одного path — побеждает ``<file>`` (явный полный replace > патч).
    Edit с конфликтом (SEARCH не нашёлся или нашёлся >1 раз) попадает в
    ``conflicts`` и в результат не входит — caller может решить попросить
    модель прислать <file> вместо.

    Возвращает ``(files_to_commit, conflicts)``. ``files_to_commit`` идёт
    дальше в обычный commit-flow.
    """
    files = extract_files(accumulated)
    edits = extract_edits(accumulated)
    if not edits:
        return files, []
    # Edit base = текущее состояние МИНУС те файлы, которые модель явно
    # переписала через <file>. Это предотвращает гонку «применили патч к
    # старой версии, потом перезаписали полной новой».
    edit_base = {p: c for p, c in base_files.items() if p not in files}
    patched, conflicts = apply_edits(edits, edit_base)
    return {**patched, **files}, conflicts


def _ensure_kit_linked(files: dict[str, str]) -> dict[str, str]:
    """Гарантировать, что каждая возвращённая HTML-страница подключает Omnia-кит.

    Если модель «уронила» теги — переинжектим их перед </head> (иначе анимации и
    интерактив тихо ломаются). Идемпотентно: страницы со ссылкой пропускаем.
    """
    out = dict(files)
    for path, content in files.items():
        if not path.lower().endswith((".html", ".htm")):
            continue
        has_css = "assets/omnia-kit.css" in content
        has_anime = "assets/anime.min.js" in content
        has_js = "assets/omnia-kit.js" in content
        if has_css and has_anime and has_js:
            continue
        inject = ""
        if not has_css:
            inject += "  " + _KIT_LINK + "\n"
        if not has_anime:
            inject += "  " + _ANIME_SCRIPT + "\n"
        if not has_js:
            inject += "  " + _KIT_SCRIPT + "\n"
        if "</head>" in content:
            content = content.replace("</head>", inject + "</head>", 1)
        else:
            content = inject + content
        out[path] = content
    return out


def _with_vendor_directive(
    messages: list[dict[str, str]], model_id: str, *, json_strict: bool
) -> list[dict[str, str]]:
    """Return a copy of ``messages`` with the per-vendor block appended to the
    LAST user turn. The system turn stays byte-identical → Anthropic prompt
    cache still hits. No-op (returns the same list) for GENERIC/uncalibrated
    models so there's zero regression on models we haven't tuned.

    Used by the single-shot / freeform path; the catalog Director→Polish and
    multipass paths inject the directive in their own message builders.
    """
    directive = vendor_directive(model_id, json_strict=json_strict)
    if not directive:
        return messages
    out = list(messages)
    for i in range(len(out) - 1, -1, -1):
        if out[i].get("role") == "user":
            turn = dict(out[i])
            turn["content"] = f"{turn['content']}\n\n{directive}"
            out[i] = turn
            break
    return out
