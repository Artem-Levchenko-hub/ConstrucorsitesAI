"""Парсер AI-ответа в формате <file path="...">...</file> + санитизация путей.

Поддерживает ДВА формата:
* ``<file path="...">{full content}</file>`` — для новых файлов или полных
  пересборок. Body заменяет содержимое целиком.
* ``<edit path="...">`` с одной или несколькими SEARCH/REPLACE-секциями
  внутри (aider-style). Body парсится, каждая секция применяется к
  существующему содержимому файла. Намного дешевле по токенам когда
  правка маленькая (точечная замена кнопки, цвета, текста), потому что
  модель не переписывает весь файл.

Парсер этого модуля чисто-функциональный: на входе текст ответа AI, на
выходе словарь "что записать". Чтение текущих файлов для apply_edits
делает caller (см. ``routers/messages.py``).
"""

from __future__ import annotations

import logging
import re
from pathlib import PurePosixPath

from omnia_api.services.lucide_icon_names import is_valid_lucide_name

log = logging.getLogger(__name__)

_FILE_BLOCK = re.compile(
    r'<file\s+path="(?P<path>[^"]+)"\s*>(?P<body>.*?)</file>',
    re.DOTALL,
)

# `<edit path="src/index.html">...</edit>`. Body содержит ≥1 SEARCH/REPLACE.
_EDIT_BLOCK = re.compile(
    r'<edit\s+path="(?P<path>[^"]+)"\s*>(?P<body>.*?)</edit>',
    re.DOTALL,
)

# aider-style маркеры внутри <edit>. Допускаются 7-символьные `<<<<<<<` и
# `>>>>>>>` (классика), а также любая длина 6-9 — некоторые модели сбиваются.
# `=======` обязателен ровно 7-символьный (это безопасно — модель сама редко
# отклоняется).
_SR_BLOCK = re.compile(
    r"<{6,9}\s*SEARCH\s*\n(?P<search>.*?)\n={7}\s*\n(?P<replace>.*?)\n>{6,9}\s*REPLACE",
    re.DOTALL,
)

_FORBIDDEN_PREFIXES = ("/", "~", ".git/", ".git\\")
_FORBIDDEN_SUBSTRINGS = ("..",)
MAX_FILES = 100
MAX_FILE_BYTES = 2 * 1024 * 1024

# The app writer reliably invents a marketing palette of CSS custom properties
# for landing/auth pages — var(--bg)/var(--fg)/var(--muted)/var(--accent)/
# var(--bg-alt) — and NEVER emits a :root block defining them. --bg/--fg/--bg-alt
# then resolve to nothing; worse, --muted/--accent COLLIDE with the kit's shadcn
# surface tokens in globals.css (oklch≈0.97, near-white), so text-[var(--muted)]/
# text-[var(--accent)] paint near-white text on a white page = invisible. A
# negative brief rule doesn't dislodge this strong prior, so we rewrite the
# arbitrary-value USAGES to the real kit tokens deterministically. Only usages
# (`var(--X)`) are touched, never a `--X:` DEFINITION, and only in generated
# source — the kit components use Tailwind utility classes (`bg-accent`), not
# `var(--accent)`, so they're left untouched. Order matters: rewrite --muted/
# --accent BEFORE re-introducing var(--muted) via --bg-alt so the alt background
# stays a light surface, not muted-foreground text colour.
_PALETTE_VAR_FIXES: tuple[tuple[str, str], ...] = (
    ("var(--muted)", "var(--muted-foreground)"),
    ("var(--accent)", "var(--primary)"),
    ("var(--bg-alt)", "var(--muted)"),
    ("var(--bg)", "var(--background)"),
    ("var(--fg)", "var(--foreground)"),
)
_PALETTE_FIX_SUFFIXES = (".tsx", ".jsx", ".ts", ".css")


def _fix_invented_palette_vars(path: str, body: str) -> str:
    """Rewrite the writer's invented landing-palette CSS vars to real kit tokens.

    See ``_PALETTE_VAR_FIXES``. No-op unless ``path`` is a generated source file
    that actually contains an invented usage, so correctly-authored files pass
    through byte-identical (R-10 fail-soft)."""
    if not path.endswith(_PALETTE_FIX_SUFFIXES):
        return body
    for invented, token in _PALETTE_VAR_FIXES:
        if invented in body:
            body = body.replace(invented, token)
    return body


# The writer hallucinates lucide-react imports that the package does NOT export.
# Two flavours, both fatal: brand/social logos (`Telegram`, lucide ships generic
# glyphs not company logos) AND abbreviated/wrong forms of real icons (`Trend`
# for `TrendingUp`, `Dashboard` for `LayoutDashboard`, `Cart` for `ShoppingCart`).
# Any one of them breaks the Turbopack build outright — the dev container never
# serves 200, so the WHOLE generated app is dead. A negative brief rule doesn't
# stop the model reaching for `<Trend/>`, so we repair the IMPORT deterministically:
# every imported specifier is validated against the canonical lucide export set
# (`is_valid_lucide_name`); an unknown name is aliased to a valid glyph — a
# visually-adjacent brand fallback when we have one, else a neutral `Circle` — while
# its `<Name/>` usages keep their original local name via the alias. Valid names
# (including the `<Base>Icon` / `Lucide<Base>` alias forms) pass through unchanged.
#
# The brand table below is the *nicer-looking* fallback layer (Telegram→Send beats
# Telegram→Circle); keyed by lower-cased imported name so casing variants all map.
# Keys are confirmed ABSENT and values confirmed PRESENT in the template's lucide.
_LUCIDE_GENERIC_FALLBACK = "Circle"  # always a valid lucide export; neutral glyph
_LUCIDE_INVALID_ICON_FALLBACKS: dict[str, str] = {
    "telegram": "Send",
    "whatsapp": "MessageCircle",
    "vk": "Share2",
    "vkontakte": "Share2",
    "tiktok": "Music2",
    "discord": "MessageSquare",
    "pinterest": "Image",
    "reddit": "MessageCircle",
    "behance": "Palette",
    "snapchat": "Camera",
    "threads": "AtSign",
    "spotify": "Music2",
    "tumblr": "Hash",
    "skype": "Phone",
    "wechat": "MessageCircle",
    "viber": "Phone",
    "medium": "BookOpen",
    "google": "Globe",
    "paypal": "CreditCard",
    "visa": "CreditCard",
    "mastercard": "CreditCard",
}
_LUCIDE_FIX_SUFFIXES = (".tsx", ".jsx", ".ts")

# Named-import block from the bare "lucide-react" module: captures the brace body
# so we can rewrite individual specifiers. `[^{}]` spans newlines (multiline
# import lists) but stops at a nested brace (there are none in an import list).
_LUCIDE_IMPORT_BLOCK = re.compile(
    r'import\s+\{(?P<names>[^{}]*)\}\s+from\s+(?P<q>["\'])lucide-react(?P=q)',
    re.DOTALL,
)
_AS_SPLIT = re.compile(r"\s+as\s+")

# Capitalised JSX opening tags: `<Briefcase`, `<Briefcase/>`, `<Briefcase ... >`.
# A leading capital is React's component-vs-host-element rule, so this is exactly
# the set of identifiers that must resolve to a binding at runtime.
_JSX_TAG = re.compile(r"<([A-Z][A-Za-z0-9]*)")
# Each import statement's binding clause (everything between `import` and `from`),
# robust to multiline named-import lists. Used to collect already-bound names.
_IMPORT_STMT = re.compile(
    r'import\b(?P<binding>[^;]*?)\bfrom\s*["\'][^"\']+["\']', re.DOTALL
)
_IDENT = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
# Local component declarations whose name could shadow a lucide icon.
_LOCAL_DECL = re.compile(r"\b(?:function|class|const|let|var)\s+([A-Z][A-Za-z0-9]*)")


def _fix_invalid_lucide_imports(path: str, body: str) -> str:
    """Alias every invalid lucide import to a real glyph so the build survives.

    Each imported specifier is checked against the canonical lucide export set
    (``is_valid_lucide_name``). A valid name — including ``<Base>Icon`` /
    ``Lucide<Base>`` alias forms — passes through byte-identical. An unknown name is
    aliased to a valid glyph: a visually-adjacent brand fallback when one is known,
    else ``Circle``. No-op unless ``path`` is a generated source file importing from
    ``lucide-react`` (R-10 fail-soft). Only the import block is rewritten — usages
    keep their original local name via the alias."""
    if not path.endswith(_LUCIDE_FIX_SUFFIXES) or "lucide-react" not in body:
        return body

    def _rewrite(match: re.Match[str]) -> str:
        changed = False
        specs: list[str] = []
        for raw in match.group("names").split(","):
            spec = raw.strip()
            if not spec:
                continue
            parts = _AS_SPLIT.split(spec, 1)
            imported = parts[0].strip()
            local = parts[1].strip() if len(parts) > 1 else imported
            if is_valid_lucide_name(imported):
                specs.append(spec)
                continue
            fallback = _LUCIDE_INVALID_ICON_FALLBACKS.get(
                imported.lower(), _LUCIDE_GENERIC_FALLBACK
            )
            specs.append(f"{fallback} as {local}")
            changed = True
        if not changed:
            return match.group(0)
        return match.group(0).replace(
            match.group("names"), " " + ", ".join(specs) + " "
        )

    return _LUCIDE_IMPORT_BLOCK.sub(_rewrite, body)


# The inverse failure of the fix above: the writer RENDERS a real lucide icon in
# JSX (`icon={<Briefcase />}`) but forgets to import it. The name is a valid lucide
# export, so `_fix_invalid_lucide_imports` leaves it alone — and at runtime React
# throws "Briefcase is not defined", crashing the whole page (the generated CRM
# dashboard served a blank client-side-exception screen on exactly this bug). A
# negative brief rule can't stop the model dropping an import, so we repair it
# deterministically.
def _fix_missing_lucide_imports(path: str, body: str) -> str:
    """Add used-but-unimported lucide icons to the import (R-10 fail-soft).

    A capitalised JSX tag that is a real lucide export but is bound nowhere in the
    file (no import from any module, no local declaration) would throw
    ``<Name> is not defined`` at runtime and crash the page. Such names are appended
    to the existing ``lucide-react`` import block, or a fresh
    ``import { ... } from "lucide-react"`` line is inserted after the last import.
    No-op on non-source files and when nothing is missing (byte-identical). Names
    bound elsewhere — including lucide icons that double as kit/ui components like
    ``Badge`` — are skipped so we never emit a duplicate declaration."""
    if not path.endswith(_LUCIDE_FIX_SUFFIXES):
        return body

    candidates = {
        m.group(1) for m in _JSX_TAG.finditer(body) if is_valid_lucide_name(m.group(1))
    }
    if not candidates:
        return body

    bound: set[str] = set()
    for stmt in _IMPORT_STMT.finditer(body):
        bound.update(_IDENT.findall(stmt.group("binding")))
    bound.update(_LOCAL_DECL.findall(body))

    missing = sorted(candidates - bound)
    if not missing:
        return body

    block = _LUCIDE_IMPORT_BLOCK.search(body)
    if block is not None:
        existing = [s.strip() for s in block.group("names").split(",") if s.strip()]
        merged = " " + ", ".join(existing + missing) + " "
        return body[: block.start("names")] + merged + body[block.end("names") :]

    new_line = f'import {{ {", ".join(missing)} }} from "lucide-react";'
    imports = list(_IMPORT_STMT.finditer(body))
    if not imports:
        return new_line + "\n" + body
    nl = body.find("\n", imports[-1].end())
    if nl == -1:
        return body + "\n" + new_line
    return body[: nl + 1] + new_line + "\n" + body[nl + 1 :]


# The writer reaches for a `toast` helper and bundles it into ANOTHER module's
# named import — almost always `@/lib/utils` (`import { formatDate, toast } from
# "@/lib/utils"`). But the kit's utils module exports no `toast`; toasts come from
# `sonner` (`import { toast } from "sonner"`, exactly as the kit's own
# crud-resource/entity-form components do). Turbopack STATICALLY validates named
# exports and fails the build outright ("Export toast doesn't exist in target
# module"), so the whole route serves the dev error overlay = a dead section. A
# brief rule can't dislodge the reflex, so we relocate the misrouted symbol
# deterministically: drop it from the wrong module's import and (re)add it to its
# canonical module's import.
#
# Only symbols with a KNOWN canonical kit home are relocated (currently `toast`);
# an unknown invalid name (e.g. a hallucinated `formatPrice` from utils) is left
# untouched — we never invent a home we can't verify, and dropping it would create
# an undefined reference (R-10: never worsen). The fix fires only when the symbol is
# imported from the WRONG module and isn't already imported from the right one.
_KIT_SYMBOL_CANONICAL_SOURCE: dict[str, str] = {
    "toast": "sonner",
}
_MISROUTE_FIX_SUFFIXES = (".tsx", ".jsx", ".ts")
# Any `import { a, b as c } from "<module>"` statement (trailing `;` optional).
# `[^{}]` spans newlines but stops at a nested brace (none in an import list).
_ANY_NAMED_IMPORT = re.compile(
    r'import\s+\{(?P<names>[^{}]*)\}\s+from\s+(?P<q>["\'])(?P<module>[^"\']+)(?P=q)\s*;?',
    re.DOTALL,
)


def _named_import_block(module: str) -> re.Pattern[str]:
    """Regex matching a named-import block from one specific ``module``."""
    return re.compile(
        r"import\s+\{(?P<names>[^{}]*)\}\s+from\s+(?P<q>[\"'])"
        + re.escape(module)
        + r"(?P=q)\s*;?",
        re.DOTALL,
    )


def _iter_specs(names: str) -> list[str]:
    """Non-empty, stripped specifiers from a named-import brace body."""
    return [s.strip() for s in names.split(",") if s.strip()]


def _imported_name(spec: str) -> str:
    """The exported name a specifier binds (left of ``as``): ``toast as t`` → ``toast``."""
    return _AS_SPLIT.split(spec, 1)[0].strip()


def _insert_after_last_import(body: str, line: str) -> str:
    """Insert ``line`` right after the last import statement (or at the top)."""
    imports = list(_IMPORT_STMT.finditer(body))
    if not imports:
        return line + "\n" + body
    nl = body.find("\n", imports[-1].end())
    if nl == -1:
        return body + "\n" + line
    return body[: nl + 1] + line + "\n" + body[nl + 1 :]


def _fix_misrouted_kit_imports(path: str, body: str) -> str:
    """Relocate a kit symbol imported from the wrong module to its canonical one.

    A symbol with a known canonical home (``toast`` → ``sonner``) imported from a
    different module fails Turbopack's static export check and kills the route. Such
    a symbol is dropped from the wrong import and (re)added to its canonical module's
    import — merged into an existing one, or inserted as a fresh line. No-op unless
    ``path`` is a generated source file that actually misroutes a known symbol
    (byte-identical otherwise, R-10 fail-soft); unknown invalid names are untouched."""
    if not path.endswith(_MISROUTE_FIX_SUFFIXES):
        return body
    if not any(sym in body for sym in _KIT_SYMBOL_CANONICAL_SOURCE):
        return body

    # Symbols already imported from their canonical module — never duplicate those.
    already: set[str] = set()
    for m in _ANY_NAMED_IMPORT.finditer(body):
        for spec in _iter_specs(m.group("names")):
            name = _imported_name(spec)
            if _KIT_SYMBOL_CANONICAL_SOURCE.get(name) == m.group("module"):
                already.add(name)

    relocate: dict[str, list[str]] = {}  # canonical module -> specs to add
    removed_any = False

    def _strip_wrong(m: re.Match[str]) -> str:
        nonlocal removed_any
        module = m.group("module")
        kept: list[str] = []
        moved: list[str] = []
        for spec in _iter_specs(m.group("names")):
            canonical = _KIT_SYMBOL_CANONICAL_SOURCE.get(_imported_name(spec))
            if canonical is not None and canonical != module:
                moved.append(spec)
            else:
                kept.append(spec)
        if not moved:
            return m.group(0)
        removed_any = True
        for spec in moved:
            name = _imported_name(spec)
            if name in already:
                continue  # already imported from canonical → just drop the dup
            relocate.setdefault(_KIT_SYMBOL_CANONICAL_SOURCE[name], []).append(spec)
            already.add(name)  # don't relocate the same symbol twice
        if not kept:
            return ""  # whole wrong-module import removed
        return m.group(0).replace(m.group("names"), " " + ", ".join(kept) + " ")

    new_body = _ANY_NAMED_IMPORT.sub(_strip_wrong, body)
    if not removed_any:
        return body

    for module, specs in relocate.items():
        block = _named_import_block(module).search(new_body)
        if block is not None:
            existing = _iter_specs(block.group("names"))
            bound = {_imported_name(s) for s in existing}
            add = [s for s in specs if _imported_name(s) not in bound]
            if not add:
                continue
            merged = " " + ", ".join(existing + add) + " "
            new_body = new_body[: block.start("names")] + merged + new_body[block.end("names") :]
        else:
            new_body = _insert_after_last_import(
                new_body, f'import {{ {", ".join(specs)} }} from "{module}";'
            )
    return new_body


# The writer formats prices/dates with a bare `.toLocaleString()` (no locale arg)
# on server-rendered landing/page components. The locale then defaults to the
# RUNTIME's locale, which differs between the SSR pass (the dev container's Node,
# ru-leaning -> "4 500" with a non-breaking space) and the client (the visitor's
# browser, e.g. en-US -> "4,500"). React sees server text != client text and
# throws a hydration-mismatch error, regenerating the whole tree on the client
# (visible flicker + console error). Pinning an explicit locale makes both passes
# emit the identical string regardless of runtime locale. We only touch the
# EMPTY-parens form — `toLocaleString('ru-RU')` / `toLocaleString(undefined, {…})`
# already pass a first arg and are left byte-identical.
_LOCALE_FIX_SUFFIXES = (".tsx", ".jsx", ".ts")
_BARE_LOCALE_CALL = re.compile(
    r"\.toLocale(?P<kind>String|DateString|TimeString)\(\s*\)"
)


def _fix_bare_locale_string(path: str, body: str) -> str:
    """Pin an explicit locale on bare ``toLocale*String()`` calls.

    A locale-less call resolves to the runtime locale, which differs between the
    SSR (container Node) and client (browser) passes -> hydration mismatch. Adding
    ``'ru-RU'`` makes both passes deterministic. No-op unless ``path`` is a
    generated source file with a bare call (R-10 fail-soft); calls that already
    pass an argument are untouched."""
    if not path.endswith(_LOCALE_FIX_SUFFIXES):
        return body
    return _BARE_LOCALE_CALL.sub(r".toLocale\g<kind>('ru-RU')", body)


# The writer authors a public landing as a React Server Component (App Router's
# default — no "use client" directive) yet wires an interactive form/button with
# an inline event handler: `<form onSubmit={…}>`. RSC forbids passing event
# handlers to DOM/client props, so the route throws at render time and serves a
# 500 — the whole landing is dead. The whole module is either client or server,
# so a single inline handler forces the file to be client. We prepend the
# directive deterministically. Guard: never touch a file that already declares a
# top directive, has no handler, or carries a SERVER-ONLY export (`metadata`,
# `generateMetadata`, a top-level `async` page) — those are incompatible with
# "use client", so a file mixing both is a deeper authoring bug we don't worsen.
_CLIENT_FIX_SUFFIXES = (".tsx", ".jsx")
_EVENT_HANDLER = re.compile(r"\son[A-Z][A-Za-z]+=\{")
_TOP_DIRECTIVE = re.compile(r"^﻿?\s*([\"'])use (?:client|server)\1")
_SERVER_ONLY_EXPORT = re.compile(
    r"export\s+const\s+metadata\b"
    r"|export\s+(?:async\s+)?function\s+generateMetadata\b"
    r"|export\s+default\s+async\s+function"
    r"|export\s+async\s+function\s+\w*[Pp]age"
)


def _fix_missing_use_client(path: str, body: str) -> str:
    """Prepend ``"use client"`` when a server component holds an event handler.

    An inline ``on*={…}`` handler makes a React module a client component; without
    the directive App Router renders it on the server and throws (500). No-op
    unless ``path`` is a generated ``.tsx``/``.jsx`` that lacks a top directive,
    actually has a handler, and exposes no server-only export (R-10 fail-soft —
    a file mixing server-only features with handlers is left untouched)."""
    if not path.endswith(_CLIENT_FIX_SUFFIXES):
        return body
    if _TOP_DIRECTIVE.match(body):
        return body
    if not _EVENT_HANDLER.search(body):
        return body
    if _SERVER_ONLY_EXPORT.search(body):
        return body
    return '"use client";\n\n' + body


# The writer wires a public-landing AUTH affordance — "Войти" / "Начать учиться" /
# a register CTA — to `href="/"` (or "#"), which just reloads the landing: a dead
# button that breaks the "ноль тупиков / ноль мёртвых кнопок" contract (WOW rubric
# #8). The kit ALWAYS ships real /signin and /signup routes, and a brief rule
# already tells the model to use them — yet it recurs across niches (магазин,
# онлайн-школа) because a positive brief rule can't stop the model defaulting a
# second auth control to "/". `_fix_dead_internal_links` won't catch it either: "/"
# resolves to the real landing route, so it isn't a 404. We repair it
# deterministically: a Link/anchor whose VISIBLE TEXT reads as a login/sign-up
# affordance AND whose href is the dead self-link ("/", "#", empty) is repointed to
# /signin (login words) or /signup (register/"start" words). The double guard (auth
# text AND dead href) leaves every other "/" link — logo, "на главную", #-anchor
# CTAs (#courses), and variable hrefs (href={link.href}) — byte-identical.
_AUTH_FIX_SUFFIXES = (".tsx", ".jsx")
_LOGIN_TEXT = ("войти", "вход", "авторизац", "log in", "login", "sign in", "signin")
_SIGNUP_TEXT = (
    "регистрац",
    "зарегистр",
    "создать аккаунт",
    "начать",
    "начни",
    "попробовать",
    "sign up",
    "signup",
    "register",
    "get started",
)
_TAG_STRIP = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_AUTH_LINK_EL = re.compile(
    r"<(?P<tag>Link|a)\b(?P<before>[^>]*?\bhref=)"
    r"""(?:(?P<q1>["'])(?P<h1>/|#|)(?P=q1)"""
    r"""|\{\s*(?P<q2>["'])(?P<h2>/|#|)(?P=q2)\s*\})"""
    r"(?P<after>[^>]*?)>(?P<inner>.*?)</(?P=tag)>",
    re.DOTALL,
)


def _auth_link_dest(inner_text: str) -> str | None:
    """`/signin` for a login affordance, `/signup` for a register/start one, else None."""
    if any(w in inner_text for w in _LOGIN_TEXT):
        return "/signin"
    if any(w in inner_text for w in _SIGNUP_TEXT):
        return "/signup"
    return None


def _fix_dead_auth_links(path: str, body: str) -> str:
    """Repoint a dead public-landing auth CTA (`href="/"`) to /signin or /signup.

    A ``<Link>``/``<a>`` whose visible text reads as a login or sign-up affordance
    and whose href is a dead self-link (``/``, ``#``, empty) is a dead button (it
    just reloads the landing) — it must reach the kit's real /signin or /signup.
    Every other "/" link (logo, "на главную", populated ``#``-anchors, variable
    hrefs) fails the text or href guard and is byte-identical. No-op on non-source
    files (R-10 fail-soft)."""
    if not path.endswith(_AUTH_FIX_SUFFIXES):
        return body

    def _rewrite(m: re.Match[str]) -> str:
        text = _WS.sub(" ", _TAG_STRIP.sub(" ", m.group("inner"))).strip().lower()
        dest = _auth_link_dest(text)
        if dest is None:
            return m.group(0)
        quote = m.group("q1") or m.group("q2") or '"'
        return (
            f"<{m.group('tag')}{m.group('before')}{quote}{dest}{quote}"
            f"{m.group('after')}>{m.group('inner')}</{m.group('tag')}>"
        )

    return _AUTH_LINK_EL.sub(_rewrite, body)


# Routes the template SHIPS but the model never re-generates in its `<file>`
# answer: /signin, /signup, /signout. A model-authored link to one of these is
# CORRECT, but `_collect_routes` only sees generated pages, so without this seed
# `_fix_dead_internal_links` would treat `href="/signin"` as a dead end and
# "repair" it to its nearest generated ancestor ("/") — silently turning every
# correct login link into the very "Войти → /" dead button we're trying to kill.
_KIT_ROUTES: tuple[tuple[str, ...], ...] = (("signin",), ("signup",), ("signout",))


# The writer sometimes wires a CTA to a route it never generates — e.g. a
# dashboard button `<Link href="/dashboard/appointments/new">` with no
# `app/(app)/dashboard/appointments/new/page.tsx` in the answer. Clicking it is a
# 404 dead-end, which violates the "ноль тупиков" (zero dead-ends) product
# contract. We cannot author the missing route deterministically (that needs the
# model), but we CAN keep the link working: rewrite a dead internal link to the
# deepest ANCESTOR route that the SAME answer actually generated (it usually has
# `/dashboard` at minimum). The user lands on a real, related page instead of a
# 404. This is cross-file, so it runs as a post-pass over the whole file set.
#
# Safety (R-10 fail-soft, "never worsen a working link"):
# * Only literal, COMPLETE internal hrefs are touched — `href="/..."`,
#   `href='/...'`, `href={"/..."}`. Interpolated/concatenated hrefs
#   (`href={`/x/${id}`}`, `href={"/x/" + id}`) and external/hash/mailto/tel are
#   left alone, so dynamic links to `[id]` routes are never corrupted.
# * Rewrite fires ONLY when the target is not itself a generated route AND a
#   shallower ancestor IS one in this answer. A link whose ancestors are also
#   absent (likely a route authored in a different turn) is left untouched.
# * extract_files only sees full `<file>` builds (edit-turns carry `<edit>`
#   blocks → no `<file>` → this no-ops), so on the turn it fires the route set
#   is complete.
_LINK_FIX_SUFFIXES = (".tsx", ".jsx")
_PAGE_FILE = re.compile(r"(?:^|/)page\.(?:tsx|jsx|ts|js)$")
# Complete literal internal href: bare `"/..."`/`'/...'` or a brace that closes
# immediately after the string (`{"/..."}`). Concatenation/interpolation inside
# the brace fails the trailing `\s*\}` and is skipped.
_HREF = re.compile(
    r"""href=
        (?:
            (?P<q1>["'])(?P<u1>/[^"']*)(?P=q1)
            |
            \{\s*(?P<q2>["'])(?P<u2>/[^"']*)(?P=q2)\s*\}
        )
    """,
    re.VERBOSE,
)


def _route_segments_for_key(key: str) -> list[str] | None:
    """URL route segments for a generated ``page.*`` file key, or ``None``.

    Strips a leading ``src/``, everything up to and including the ``app/`` root,
    the trailing ``page.*`` file, and App-Router route-group segments (``(...)``).
    Dynamic (``[id]``) and catch-all (``[...x]``) segments are kept verbatim for
    pattern matching. A page directly under ``app/`` maps to ``[]`` (the ``/``
    route)."""
    if not _PAGE_FILE.search(key):
        return None
    norm = key[4:] if key.startswith("src/") else key
    parts = norm.split("/")
    if "app" not in parts:
        return None
    after = parts[parts.index("app") + 1 : -1]  # drop the page.* filename
    return [seg for seg in after if not (seg.startswith("(") and seg.endswith(")"))]


def _one_seg_match(pat: str, target: str) -> bool:
    # A dynamic segment ``[id]`` matches any single non-empty literal.
    return pat == target or (pat.startswith("[") and pat.endswith("]") and bool(target))


def _seg_match(target: list[str], pat: list[str]) -> bool:
    if pat and (pat[-1].startswith("[...") or pat[-1].startswith("[[...")):
        head = pat[:-1]
        optional = pat[-1].startswith("[[")
        min_len = len(head) if optional else len(head) + 1
        if len(target) < min_len:
            return False
        return all(_one_seg_match(p, t) for p, t in zip(head, target, strict=False))
    if len(target) != len(pat):
        return False
    return all(_one_seg_match(p, t) for p, t in zip(pat, target, strict=True))


def _collect_routes(files: dict[str, str]) -> list[list[str]]:
    """Route patterns (segment lists) for every non-empty generated page.

    Seeded with the kit-shipped routes (/signin, /signup, /signout) the model
    links to but never generates, so a correct auth link is never "repaired" away."""
    routes: list[list[str]] = [list(r) for r in _KIT_ROUTES]
    for key, body in files.items():
        if not body.strip():
            continue  # an emptied page.tsx is a DELETE, not a route
        segs = _route_segments_for_key(key)
        if segs is not None:
            routes.append(segs)
    return routes


def _route_exists(target: list[str], routes: list[list[str]]) -> bool:
    return any(_seg_match(target, pat) for pat in routes)


def _nearest_ancestor(target: list[str], routes: list[list[str]]) -> str | None:
    """Deepest existing ancestor URL of a dead ``target``, or ``None``.

    Walks ``target`` shorter one segment at a time; the first shortened path that
    resolves to a generated route wins. ``[]`` is the root ``/``."""
    for cut in range(len(target) - 1, -1, -1):
        ancestor = target[:cut]
        if _route_exists(ancestor, routes):
            return "/" + "/".join(ancestor)
    return None


def _fix_dead_internal_links(files: dict[str, str]) -> None:
    """Rewrite dead internal links to their nearest generated ancestor in place.

    No-op unless the answer carries at least one generated page (route set) and a
    link points below the deepest route that exists (R-10 fail-soft — anything
    uncertain is left untouched)."""
    routes = _collect_routes(files)
    if not routes:
        return

    def _rewrite(match: re.Match[str]) -> str:
        raw = match.group("u1") or match.group("u2")
        if raw.startswith("//"):  # protocol-relative — external, leave alone
            return match.group(0)
        path = raw.split("#", 1)[0].split("?", 1)[0].rstrip("/")
        target = [s for s in path.split("/") if s]
        if _route_exists(target, routes):
            return match.group(0)
        ancestor = _nearest_ancestor(target, routes)
        if ancestor is None:
            return match.group(0)  # no anchor in this answer → cross-turn, skip
        quote = match.group("q1") or match.group("q2")
        if match.group("u1") is not None:
            return f"href={quote}{ancestor}{quote}"
        return f"href={{{quote}{ancestor}{quote}}}"

    for path, body in files.items():
        if not path.endswith(_LINK_FIX_SUFFIXES):
            continue
        new_body = _HREF.sub(_rewrite, body)
        if new_body != body:
            files[path] = new_body
            log.info("extract_files: rewrote dead internal link(s) in %r", path)


# Two deterministic, 100%-fatal nextjs-entities build-killers the writer recurrently
# emits. Unlike ``structure_audit`` (which only WARNS for observability), these are
# repaired here because the outcome is binary — the dev container never serves 200,
# so the WHOLE generated app is dead — and the fix is unambiguous. Both fire only on
# the exact fatal shape, so a static/freeform build (single index.html, no
# globals.css, no ``(app)`` group) passes through untouched (R-10 fail-soft).
#
# Killer 1 — globals.css rewritten in Tailwind v3 syntax. The template ships a FIXED
# v4 ``@theme``/token globals.css INSIDE the container image; the writer must never
# author one. When it emits a v3 ``@tailwind ...`` / ``@apply border-border`` file
# the v4 build dies ("unknown utility class border-border"). Fix: DISCARD the key
# entirely — NOT "" (which is delete-intent → the orchestrator ``rm -f``s the path in
# the container, deleting the image's good globals.css too → an even worse build with
# no globals.css at all). Dropping the key just never writes/syncs the writer's bad
# file, so the image's fixed v4 globals.css is neither shadowed nor removed.
#
# Killer 2 — a non-empty starter ``src/app/page.tsx`` left ALONGSIDE
# ``src/app/(app)/page.tsx``. Both resolve to "/", so ``next build`` refuses (route
# conflict). The writer is told to empty the starter; when it forgets, fix: empty it
# ("" IS correct here — page.tsx is a real repo file we WANT deleted), handing "/" to
# the (app) dashboard.
_GLOBALS_V3_SIGNATURES = ("@tailwind ", "@apply border-border")


def _fix_app_killer_bugs(files: dict[str, str]) -> None:
    """Repair the two deterministic, 100%-fatal app build-killers in place.

    No-op on any answer that doesn't carry the exact fatal shape (R-10 fail-soft)."""
    globals_css = files.get("src/app/globals.css")
    if isinstance(globals_css, str) and any(
        sig in globals_css for sig in _GLOBALS_V3_SIGNATURES
    ):
        del files["src/app/globals.css"]
        log.info(
            "extract_files: discarded writer's Tailwind-v3 globals.css — image's "
            "fixed v4 token file kept (v3 syntax breaks the v4 build)"
        )

    starter = files.get("src/app/page.tsx")
    app_index = files.get("src/app/(app)/page.tsx")
    if (
        isinstance(starter, str)
        and starter.strip()
        and isinstance(app_index, str)
        and app_index.strip()
    ):
        files["src/app/page.tsx"] = ""
        log.info(
            "extract_files: emptied starter src/app/page.tsx — (app)/page.tsx owns "
            "'/' (both resolving to '/' is a fatal route conflict)"
        )


# The APP writer themes the product with a SINGLE inline <style> in
# ``(app)/layout.tsx`` overriding --primary/--primary-foreground/--ring in oklch
# (globals.css is fixed and never touched, so this inline override is the ONLY
# app-theme knob). It is model-trusted and unguarded — palette_guard/contrast_guard
# are .html-only — so a malformed oklch (bad/missing L·C·H) or a --primary whose
# lightness sits too close to its foreground ships unreadable buttons / a half-broken
# theme. We validate the override deterministically and, on violation, DROP the whole
# inline :root override so the kit's proven neutral default theme stays in force — a
# broken brand colour is worse than the safe default (R-10 fail-soft).
_APP_LAYOUT_SUFFIX = "(app)/layout.tsx"
# A JSX inline style whose string child carries a ``:root{...}`` override block.
_INLINE_ROOT_STYLE = re.compile(
    r"<style>\s*\{\s*(['\"])(?P<css>.*?:root\s*\{.*?)\1\s*\}\s*</style>",
    re.DOTALL,
)
_OKLCH_DECL = re.compile(r"(--[\w-]+)\s*:\s*oklch\(\s*([^)]*)\)", re.IGNORECASE)
# Min perceptual-lightness gap (oklch L is 0..1) between --primary and its
# foreground for legible button text. 0.40 is a conservative, comfortable floor.
_MIN_PRIMARY_FG_L_GAP = 0.40
# Kit default --primary-foreground is near-white; used when the override omits it.
_DEFAULT_PRIMARY_FG_L = 0.985


def _oklch_lightness(value: str) -> float | None:
    """Parse the L component (0..1) of an ``oklch(L C H[ / a])`` value.

    Returns None if the value is malformed (missing/garbage L·C·H, out-of-range).
    Accepts space- or comma-separated components, a percent L, and a trailing
    ``/ alpha``."""
    body = value.replace(",", " ").split("/", 1)[0]
    parts = body.split()
    if len(parts) < 3:
        return None
    try:
        lightness = float(parts[0].rstrip("%"))
        chroma = float(parts[1])
        hue = float(parts[2])
    except ValueError:
        return None
    if parts[0].endswith("%"):
        lightness /= 100.0
    if not (0.0 <= lightness <= 1.0) or chroma < 0.0 or not (0.0 <= hue <= 360.0):
        return None
    return lightness


def _app_theme_override_is_broken(css: str) -> bool:
    """True when the inline :root override has a malformed oklch, or a --primary too
    close in lightness to its foreground to be legible."""
    lightness: dict[str, float] = {}
    for name, val in _OKLCH_DECL.findall(css):
        parsed = _oklch_lightness(val)
        if parsed is None:
            return True  # a malformed oklch breaks the theme outright
        lightness[name.lower()] = parsed
    primary = lightness.get("--primary")
    if primary is None:
        return False  # override doesn't touch the brand colour — nothing to judge
    fg = lightness.get("--primary-foreground", _DEFAULT_PRIMARY_FG_L)
    return abs(primary - fg) < _MIN_PRIMARY_FG_L_GAP


def _fix_app_layout_theme(path: str, body: str) -> str:
    """Drop the inline :root theme override in ``(app)/layout.tsx`` when its oklch is
    malformed or low-contrast, so the kit default theme stays. No-op otherwise."""
    if not path.endswith(_APP_LAYOUT_SUFFIX):
        return body

    def _strip(match: re.Match[str]) -> str:
        if _app_theme_override_is_broken(match.group("css")):
            log.info(
                "extract_files: dropped broken inline app theme override in %r "
                "(malformed/low-contrast oklch) — kit default theme kept",
                path,
            )
            return ""
        return match.group(0)

    return _INLINE_ROOT_STYLE.sub(_strip, body)


# Models occasionally violate the "unchanged files: don't mention" contract
# and return a <file> block whose body is a human-language placeholder like
# "(код без изменений)". Writing that into the file produces a TypeScript /
# Python / HTML parse error and breaks the dev container. We detect such
# stubs and silently skip them — the prior file content stays intact.
_PLACEHOLDER_SIGNATURES = (
    "код без изменений",
    "без изменений",
    "не изменён",
    "не изменен",
    "unchanged",
    "no changes",
    "no change",
    "same as before",
    "as is",
    "оставить как есть",
)


class UnsafePathError(ValueError):
    pass


def is_safe_path(path: str) -> bool:
    if not path or path.startswith(_FORBIDDEN_PREFIXES):
        return False
    if any(s in path for s in _FORBIDDEN_SUBSTRINGS):
        return False
    if "\x00" in path:
        return False
    try:
        normalized = PurePosixPath(path)
    except (ValueError, TypeError):
        return False
    if normalized.is_absolute() or normalized.anchor:
        return False
    return True


_CODE_SYNTAX_CHARS = set("{};=()<>[]")


def _looks_like_unchanged_stub(body: str) -> bool:
    """True when the body is a placeholder ("no changes") instead of real content.

    Heuristic:
    * short body (<= 80 chars after strip),
    * contains a known placeholder signature,
    * AND has essentially no code syntax — `{`, `}`, `(` (other than wrapping the
      phrase), `;`, `=`, `<`, `>`, `[`, `]`. Real code of any length normally
      has at least a couple of these; "(код без изменений)" has only `(` and `)`.

    Keeping the threshold tight (<=80) prevents false-positives on real code
    that happens to mention "unchanged" in a comment or identifier.
    """
    stripped = body.strip()
    if not stripped or len(stripped) > 80:
        return False
    low = stripped.lower()
    if not any(sig in low for sig in _PLACEHOLDER_SIGNATURES):
        return False
    # Strip the most common wrappers: ()/<!--/-->/// and whitespace, then count
    # remaining code-syntax chars. Wrapper parens around a Russian phrase are
    # not code; semicolons/braces/equals/angle brackets in a 80-char body are.
    significant_syntax = sum(1 for ch in stripped if ch in {"{", "}", ";", "=", "[", "]"})
    if significant_syntax > 0:
        return False
    return True


def extract_files(answer: str) -> dict[str, str]:
    files: dict[str, str] = {}
    for match in _FILE_BLOCK.finditer(answer):
        raw_path = match.group("path").strip()
        body = match.group("body")
        # A whitespace-only body (incl. a lone "\n") is the model's way of asking
        # to DROP a file — the app writer empties the starter src/app/page.tsx
        # exactly so, to hand "/" to (app)/page.tsx. Normalise to "" so the
        # downstream git-commit (unlink) and orchestrator hot_reload (rm) both
        # treat it as delete-intent; a 1-byte "\n" would otherwise be written as
        # a broken module and crash the dev server ("default export is not a
        # React Component").
        if body.strip() == "":
            body = ""
        if not is_safe_path(raw_path):
            raise UnsafePathError(f"unsafe file path: {raw_path!r}")
        if _looks_like_unchanged_stub(body):
            log.warning(
                "extract_files: skipping placeholder stub for %r (body=%r)",
                raw_path,
                body.strip()[:80],
            )
            continue
        if len(body.encode("utf-8")) > MAX_FILE_BYTES:
            raise ValueError(f"file {raw_path} exceeds {MAX_FILE_BYTES} bytes")
        body = _fix_invented_palette_vars(raw_path, body)
        body = _fix_invalid_lucide_imports(raw_path, body)
        body = _fix_missing_lucide_imports(raw_path, body)
        body = _fix_misrouted_kit_imports(raw_path, body)
        body = _fix_bare_locale_string(raw_path, body)
        body = _fix_missing_use_client(raw_path, body)
        body = _fix_dead_auth_links(raw_path, body)
        body = _fix_app_layout_theme(raw_path, body)
        files[raw_path] = body
        if len(files) > MAX_FILES:
            raise ValueError(f"too many files in answer: {len(files)} > {MAX_FILES}")
    # Cross-file post-passes: need the full file/route set, so they run after loop.
    _fix_dead_internal_links(files)
    _fix_app_killer_bugs(files)
    return files


class EditConflict(ValueError):
    """SEARCH-блок не нашёлся или нашёлся несколько раз — не можем
    однозначно применить замену."""


def extract_edits(answer: str) -> dict[str, list[tuple[str, str]]]:
    """Парсит `<edit path="...">` блоки в `{path: [(search, replace), ...]}`.

    Каждый <edit>-блок может содержать несколько SEARCH/REPLACE секций; они
    применяются в порядке появления (`apply_edits`). Здесь только парсинг —
    проверка матчинга и применение делает `apply_edits`.

    Пустой словарь — нормально (модель просто не использовала формат).
    Невалидный path / огромный body — поднимаем те же исключения, что и
    `extract_files`, чтобы caller обрабатывал единообразно.
    """
    edits: dict[str, list[tuple[str, str]]] = {}
    for match in _EDIT_BLOCK.finditer(answer):
        raw_path = match.group("path").strip()
        body = match.group("body")
        if not is_safe_path(raw_path):
            raise UnsafePathError(f"unsafe edit path: {raw_path!r}")
        if len(body.encode("utf-8")) > MAX_FILE_BYTES:
            raise ValueError(f"edit {raw_path} exceeds {MAX_FILE_BYTES} bytes")

        pairs: list[tuple[str, str]] = []
        for sr in _SR_BLOCK.finditer(body):
            search = sr.group("search")
            replace = sr.group("replace")
            pairs.append((search, replace))
        if not pairs:
            log.warning(
                "extract_edits: <edit> for %r had no SEARCH/REPLACE blocks "
                "— skipping (model likely forgot the markers)",
                raw_path,
            )
            continue
        edits.setdefault(raw_path, []).extend(pairs)
        if len(edits) > MAX_FILES:
            raise ValueError(f"too many edited files: {len(edits)} > {MAX_FILES}")
    return edits


def _match_span(content: str, search: str) -> tuple[int, int] | None:
    """Locate ``search`` inside ``content`` and return its ``(start, end)`` char
    span, or ``None`` if it's missing OR ambiguous (>1 place).

    Two passes:
    1. **Exact** — the byte-for-byte ``str.find`` the SEARCH contract asks for.
       Unique hit wins; multiple hits are ambiguous → ``None``.
    2. **Indent-tolerant** — compare WHOLE lines stripped of leading/trailing
       whitespace. A cheap model often reproduces the right lines but with
       different indentation (the #1 reason a perfectly-correct edit fails to
       apply). Leading/trailing blank lines in the SEARCH are ignored. Only a
       UNIQUE line-window match is accepted — never guess between two.

    The fallback only forgives WHITESPACE, never content: a SEARCH that names a
    different tag/attribute than the file (e.g. a hallucinated ``data-omnia-photo``
    where the committed file has a resolved ``<img src>``) still won't match — that
    is the prompt's job to get right, not something we should fuzz over.
    """
    n_exact = content.count(search)
    if n_exact == 1:
        i = content.index(search)
        return (i, i + len(search))
    if n_exact > 1:
        return None

    s_lines = search.split("\n")
    while s_lines and not s_lines[0].strip():
        s_lines.pop(0)
    while s_lines and not s_lines[-1].strip():
        s_lines.pop()
    if not s_lines:
        return None
    s_norm = [ln.strip() for ln in s_lines]

    c_lines = content.split("\n")
    # Char offset where each content line begins (line i + its trailing "\n").
    offsets: list[int] = []
    pos = 0
    for ln in c_lines:
        offsets.append(pos)
        pos += len(ln) + 1

    window = len(s_norm)
    found: list[tuple[int, int]] = []
    for i in range(len(c_lines) - window + 1):
        if all(c_lines[i + k].strip() == s_norm[k] for k in range(window)):
            last = i + window - 1
            found.append((offsets[i], offsets[last] + len(c_lines[last])))
            if len(found) > 1:
                return None  # ambiguous — refuse to guess
    return found[0] if len(found) == 1 else None


def apply_edits(
    edits: dict[str, list[tuple[str, str]]],
    base_files: dict[str, str],
) -> tuple[dict[str, str], list[str]]:
    """Применить SEARCH/REPLACE-замены к копиям файлов из ``base_files``.

    Возвращает ``(updated, conflicts)``:
    * ``updated`` — только изменённые файлы (готовы для commit), путь→новое
      содержимое. Файлы из ``edits`` которых нет в ``base_files`` или где
      SEARCH не нашёлся (или нашёлся >1 раза) — попадают в ``conflicts``
      и в ``updated`` не входят.
    * ``conflicts`` — человекочитаемые описания, для логов / телеметрии.

    Этот частичный успех — by design (R-10 fail-soft): один сбойный edit
    не должен ломать остальные правильные правки в том же ответе. Caller
    может решить fallback: попросить модель прислать <file> для конфликтных
    файлов, или просто принять что они не изменились.
    """
    updated: dict[str, str] = {}
    conflicts: list[str] = []
    for path, pairs in edits.items():
        if path not in base_files:
            conflicts.append(
                f"{path}: edit для несуществующего файла — нужен <file> блок"
            )
            continue
        content = base_files[path]
        applied = 0
        for i, (search, replace) in enumerate(pairs, 1):
            span = _match_span(content, search)
            if span is None:
                exact = content.count(search)
                if exact > 1:
                    conflicts.append(
                        f"{path} #{i}: SEARCH-блок неоднозначен ({exact} вхождений), "
                        f"нужен больший контекст"
                    )
                else:
                    conflicts.append(
                        f"{path} #{i}: SEARCH-блок не найден уникально "
                        f"(первые 60 chars: {search[:60]!r})"
                    )
                # Пропускаем ТОЛЬКО эту пару, остальные применяем. Одна
                # непопавшая пара не должна ронять весь edit: напр. свап фоновой
                # картинки + осветление тёмного оверлея — если якорь оверлея
                # промахнулся, картинка всё равно обязана замениться. Зависимая
                # пара (её SEARCH = результат предыдущего REPLACE) просто не
                # найдётся и тоже пропустится — корректная деградация.
                continue
            start, end = span
            content = content[:start] + replace + content[end:]
            applied += 1
        if applied:
            updated[path] = content
    return updated, conflicts


# ── Honest chat content ──────────────────────────────────────────────────────
# The assistant message saved to the DB is the model's RAW output. The web UI
# renders anything NOT wrapped in a <file>/<edit>/<app-error> block as plain
# text, so a cheap model that replies conversationally (a ```html fence or bare
# HTML instead of an <edit>) dumps CODE into the chat, and an <edit> that didn't
# actually apply still draws a "Правка" chip. `clean_chat_content` rewrites the
# saved content to reflect what was REALLY committed.

_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_HTML_DOCISH_RE = re.compile(
    r"<!doctype html|<html[ >]|</(?:section|div|main|header|footer|body|article|nav)>",
    re.IGNORECASE,
)


def _looks_like_code_dump(text: str) -> bool:
    """True when a prose segment is really leaked markup, not a human sentence."""
    t = text.strip()
    if not t:
        return False
    if _HTML_DOCISH_RE.search(t):
        return True
    # Lots of angle-bracket tags relative to length → markup, not prose.
    tags = t.count("<")
    return tags >= 3 and tags * 40 >= len(t)


def _strip_code_from_prose(text: str) -> str:
    """Drop fenced code blocks; blank the whole segment if what remains is a raw
    markup dump. Returns clean human prose (possibly empty)."""
    no_fence = _CODE_FENCE_RE.sub("", text)
    if _looks_like_code_dump(no_fence):
        return ""
    return no_fence.strip()


def clean_chat_content(
    raw: str,
    committed_files: dict[str, str] | None,
    *,
    surgical: bool = False,
    fallback: str = "",
) -> str:
    """Rewrite a raw assistant answer into HONEST chat content.

    Keeps a ``<file>``/``<edit>`` chip ONLY for a path that was actually
    committed (``committed_files``), strips leaked code out of the prose, and
    synthesises a clean chip for any committed file whose original block didn't
    survive (e.g. a salvaged rewrite that streamed bare HTML). ``fallback`` is
    returned when nothing survives (a fully-failed edit) so the row is never
    blank. ``surgical`` picks the synthesised chip flavour (a compact "Правка"
    note for edits vs the full ``<file>`` body for a build).

    Pure function (R-01): no I/O, mirrors the web parser in
    apps/web/src/lib/parse-assistant.ts — update both together.
    """
    committed = committed_files or {}

    blocks: list[tuple[int, int, str]] = []  # (start, end, path)
    for rx in (_FILE_BLOCK, _EDIT_BLOCK):
        for m in rx.finditer(raw):
            blocks.append((m.start(), m.end(), m.group("path").strip()))
    blocks.sort()

    prose_segments: list[str] = []
    chips: list[str] = []
    kept_paths: set[str] = set()
    cursor = 0
    for start, end, path in blocks:
        if start > cursor:
            prose_segments.append(raw[cursor:start])
        if path in committed and path not in kept_paths:
            chips.append(raw[start:end].strip())
            kept_paths.add(path)
        cursor = max(cursor, end)
    if cursor < len(raw):
        prose_segments.append(raw[cursor:])

    prose = "\n".join(
        s for s in (_strip_code_from_prose(seg) for seg in prose_segments) if s
    ).strip()

    for path, body in committed.items():
        if path in kept_paths:
            continue
        if surgical:
            chips.append(f'<edit path="{path}">\nГотово — изменения применены.\n</edit>')
        else:
            chips.append(f'<file path="{path}">\n{body}\n</file>')

    out = "\n\n".join(part for part in [prose, *chips] if part.strip()).strip()
    return out or fallback.strip()
