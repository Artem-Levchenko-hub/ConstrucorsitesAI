"""Pure semantic font choice and text-only, frozen-install MAX delivery.

Catalogue versions bind npm integrity and inspected WOFF2 bytes. No font URL is
fetched at generation time. Existing project source is authoritative on edits.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, cast


@lru_cache(maxsize=1)
def font_catalogue() -> dict[str, Any]:
    path = Path(__file__).parents[1] / "data/typography-fontsource-v1.json"
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


@dataclass(frozen=True)
class TypographyDecision:
    version: int
    style: str
    heading: str
    body: str
    families: tuple[str, ...]
    custom_request: str | None = None
    accent: str | None = None
    ui: str | None = None
    code: str | None = None
    numeric: str | None = None
    recipe_id: str | None = None

    @property
    def dependencies(self) -> dict[str, str]:
        fonts = font_catalogue()["fonts"]
        return {fonts[key]["package"]: fonts[key]["version"] for key in self.families}

    def prompt(self) -> str:
        roles = "; ".join(
            f"{role} — {getattr(self, role)}"
            for role in ("accent", "ui", "code", "numeric")
            if getattr(self, role)
        )
        custom = (
            f"Запрошен внешний шрифт {self.custom_request!r}; он НЕ установлен. "
            "Сохрани запрос; используй system fallback до доступного разрешённого asset. "
            "Не выдумывай загрузку файла и не скачивай произвольный URL."
            if self.custom_request
            else "Локальные CSS/assets уже закреплены в package.json и pnpm-lock.yaml."
        )
        return (
            f"TypographyDecision v{self.version}: heading — {self.heading}; body — {self.body}; "
            f"style — {self.style}; {roles}. {custom} "
            "Источник истины: typography.json и src/app/typography.css. "
            "Не добавляй Google Fonts/CDN/next/font/google. "
            "При обычной правке сохрани существующий выбор; меняй только по просьбе владельца. "
            "Другие законно доступные шрифты владельца разрешены."
            " Третий шрифт используй только в указанной роли, не в основном тексте. "
            "Для цен/таблиц по умолчанию body с font-variant-numeric: tabular-nums; "
            "декоративные акценты — коротко и крупно. "
            "На мобильном проверь переносы и читабельность."
        )


_NEGATION = re.compile(
    r"\b(?:не|без|not|no|without|don't)\s+(?:(?!\s+(?:а|но|but)\s)[^,.;\n])+",
    re.I,
)
_STYLES = (
    ("scandinavian", r"сканди|scandi|nordic|минимализм|minimal|swiss", "manrope", "manrope"),
    ("editorial", r"редакцион|editorial|журнал|literary", "lora", "onest"),
    ("luxury", r"преми|luxury|люкс|элегант", "prata", "onest"),
    ("expressive", r"брутал|brutal|экспрессив|expressive|festival", "unbounded", "onest"),
    ("technical", r"футур|futur|techno|технологич", "exo-2", "onest"),
)

# Each rule describes a visual intent; niche words never trigger a third face.
# More specific combinations precede broad styles. All required expressions
# must occur in the positive brief, preserving explicit negative constraints.
_RECIPE_RULES = (
    ("R21", "technical-docs", (r"документац|documentation", r"код|code")),
    ("R22", "technical-code", (r"технологич|футур|techno|futur", r"код|code")),
    (
        "R24",
        "industrial-numeric",
        (r"индустри|industrial|плакат|poster", r"таймер|timer|табло|scoreboard"),
    ),
    ("R23", "handwritten-accent", (r"рукопис|handwritten", r"акцент|accent|подпис|signature")),
    (
        "R20",
        "literary-cover",
        (r"длинн\w* чтени|long[- ]form|long reading", r"облож|cover|воздуш|airy"),
    ),
    (
        "R19",
        "editorial-ui",
        (
            r"преми|luxury|люкс",
            r"редакцион|editorial|повеств|narrative",
            r"отдельн\w* интерфейс|separate ui",
        ),
    ),
    ("R02", "soft-minimal", (r"мягк|soft", r"минимал|minimal")),
    ("R03", "restrained-editorial", (r"сдержан|restrained", r"редакцион|editorial")),
    ("R06", "airy-literary", (r"воздуш|airy|изящ|refined", r"книж|book|элегант|elegan")),
    ("R08", "long-reading", (r"длинн\w* чтени|long[- ]form|long reading",)),
    ("R05", "craft", (r"ремесл|craft|основательн",)),
    ("R09", "cohesive-editorial", (r"цельн\w* редакцион|cohesive editorial",)),
    ("R10", "geometric", (r"современн\w* геометри|modern geometr",)),
    (
        "R11",
        "structured",
        (r"структурн|структурирован|structured|делов\w* систем|business system",),
    ),
    ("R14", "poster", (r"узк\w* плакат|narrow poster|индустри|industrial|condensed",)),
    ("R16", "soft-friendly", (r"мягк\w* дружелюб|soft friendly|округл|rounded",)),
    ("R17", "calm-neutral", (r"спокойн\w* нейтрал|calm neutral",)),
    ("R18", "dense-russian", (r"плотн\w* русск|dense russian",)),
    ("R13", "friendly-energy", (r"дружелюб\w* энерг|friendly energ",)),
    ("R04", "warm-editorial", (r"уют|тёпл|тепл|warm|cozy",)),
)
_STYLE_RECIPES = {
    "scandinavian": "R01",
    "editorial": "R04",
    "luxury": "R07",
    "expressive": "R15",
    "technical": "R12",
}
# A recipe can refine an explicitly chosen style only within that style.
# Warm/craft context otherwise outranks neither Scandinavian nor brutal intent.
_RECIPE_BASE_STYLES = {
    "R02": {"scandinavian"},
    "R03": {"editorial"},
    "R04": {"editorial"},
    "R05": {"editorial"},
    "R06": {"luxury"},
    "R08": {"editorial"},
    "R09": {"editorial"},
    "R19": {"editorial", "luxury"},
    "R20": {"editorial", "luxury"},
    "R21": {"technical", "editorial"},
    "R22": {"technical"},
    "R23": {"editorial"},
    "R24": {"technical", "expressive"},
}


def _positive_text(text: str) -> str:
    # Keep offsets, so role labels and custom spelling still refer to the brief.
    return _NEGATION.sub(lambda m: " " * len(m[0]), text).lower()


_ROLE_PATTERNS = {
    "heading": r"заголов\w*|heading|display",
    "body": r"основн\w*|текст\w*|body|paragraph",
    "accent": r"акцент\w*|accent",
    "ui": r"интерфейс\w*|ui|interface",
    "code": r"код\w*|code",
    "numeric": r"числ\w*|цифр\w*|numeric|numbers",
}
_ROLE_LABEL = r"\b(?:" + "|".join(_ROLE_PATTERNS.values()) + r")\b"


def _font_role(
    text: str,
    span: tuple[int, int],
    font_spans: list[tuple[int, int]],
) -> str | None:
    # Role-like words inside *any* full family name are data, never labels.
    masked = list(text)
    for start, end in font_spans:
        masked[start:end] = " " * (end - start)
    context = "".join(masked)
    start, end = span
    boundaries = list(re.finditer(r"[,;\n]|\b(?:и|а|and|but)\b", context))
    left = max((m.end() for m in boundaries if m.end() <= start), default=0)
    right = min((m.start() for m in boundaries if m.start() >= end), default=len(text))
    # Adjacent declarations without a comma still cannot consume each other's
    # family names. Explicit postpositions belong to the font immediately left.
    left = max(left, max((stop for _, stop in font_spans if stop <= start), default=0))
    right = min(right, min((begin for begin, _ in font_spans if begin >= end), default=len(text)))
    before, after = context[left:start], context[end:right]
    postposition = re.search(r"^\W*(?:для|for)\s+(" + _ROLE_LABEL + ")", after)
    prefix = list(re.finditer(_ROLE_LABEL, before))
    suffix = re.search(_ROLE_LABEL, after)
    role = (
        postposition[1]
        if postposition
        else prefix[-1][0]
        if prefix
        else suffix[0]
        if suffix
        else None
    )
    if role is None:
        return None
    return next(name for name, pattern in _ROLE_PATTERNS.items() if re.fullmatch(pattern, role))


def choose_typography(
    project_id: str,
    *,
    brief: str,
    preset_id: str | None = None,
) -> TypographyDecision:
    positive = _positive_text(brief)
    negative = " ".join(m[0] for m in _NEGATION.finditer(brief)).lower()
    fonts = font_catalogue()["fonts"]
    excluded = {key for key, row in fonts.items() if row["family"].lower() in negative}
    excluded_styles = {style for style, pattern, _, _ in _STYLES if re.search(pattern, negative)}
    safe = next(
        (key for key in ("onest", "manrope", "golos-text", "rubik") if key not in excluded), None
    )

    def resolve(
        style: str,
        heading: str | None,
        body: str | None,
        custom: str | None = None,
        *,
        extras: dict[str, str | None] | None = None,
        recipe_id: str | None = None,
    ) -> TypographyDecision:
        heading = safe if heading in excluded else heading
        body = safe if body in excluded else body
        extras = {role: safe if key in excluded else key for role, key in (extras or {}).items()}
        return TypographyDecision(
            1,
            style,
            fonts[heading]["family"] if heading else "system-ui",
            fonts[body]["family"] if body else "system-ui",
            tuple(dict.fromkeys(key for key in (heading, body, *extras.values()) if key)),
            custom,
            **{role: fonts[key]["family"] if key else None for role, key in extras.items()},
            recipe_id=recipe_id,
        )

    def recipe(recipe_id: str, style: str) -> TypographyDecision:
        item = next(row for row in font_catalogue()["recipes"] if row["id"] == recipe_id)
        by_name = {row["family"]: key for key, row in fonts.items()}
        roles = {role: by_name[name] for role, name in item["roles"].items()}
        heading, body = roles.pop("display"), roles.pop("body")
        return resolve(style, heading, body, extras=roles, recipe_id=recipe_id)

    custom_match = re.search(r'(?:шрифт|font)\s+["«]([\w -]{1,60})["»]', positive, re.I)
    unknown_custom = custom_match is not None and not any(
        custom_match[1] == row["family"].lower() for row in fonts.values()
    )
    mentions = []
    for key, row in fonts.items():
        for match in re.finditer(
            r"(?<!\w)" + re.escape(row["family"].lower()) + r"(?!\w)",
            positive,
        ):
            if key in excluded or (
                unknown_custom
                and custom_match is not None
                and custom_match.start(1) <= match.start() < custom_match.end(1)
            ):
                continue
            mentions.append((match.start(), match.end(), key))
    font_spans = [(start, end) for start, end, _ in mentions]
    if custom_match:
        font_spans.append(custom_match.span(1))
    custom = None
    roles: dict[str, str | None] = {}
    if unknown_custom and custom_match is not None:
        custom = brief[custom_match.start(1) : custom_match.end(1)]
        roles[_font_role(positive, custom_match.span(1), font_spans) or "heading"] = None
    unassigned = []
    for start, end, key in sorted(mentions):
        role = _font_role(positive, (start, end), font_spans)
        if role is not None:
            roles[role] = key
        else:
            unassigned.append(key)
    if mentions or custom:
        fallback = None if custom and not mentions else safe
        heading = (
            roles["heading"]
            if "heading" in roles
            else (unassigned.pop(0) if unassigned else fallback)
        )
        body = (
            roles["body"]
            if "body" in roles
            else (
                unassigned.pop(0)
                if unassigned
                else (
                    fallback
                    if heading
                    in {
                        None,
                        "lora",
                        "prata",
                        "unbounded",
                        "exo-2",
                        "caveat",
                        "cormorant-garamond",
                        "oswald",
                        "literata",
                        "vollkorn",
                        "source-serif-4",
                        "ibm-plex-mono",
                    }
                    else heading
                )
            )
        )
        extras = {role: key for role, key in roles.items() if role not in {"heading", "body"}}
        if unassigned and "accent" not in extras:
            extras["accent"] = unassigned[0]
        return resolve(
            "owner-custom-pending" if custom else "owner-font", heading, body, custom, extras=extras
        )
    # Style exclusions constrain inference, but never overrule a named owner font.
    excluded.update(heading for style, _, heading, _ in _STYLES if style in excluded_styles)
    if re.search(r"без\s+засечек|sans[- ]serif", brief, re.I):
        excluded.update(
            {"lora", "prata", "source-serif-4", "literata", "cormorant-garamond", "vollkorn"}
        )
    explicit_style = next(
        (
            style
            for source_index, source in enumerate((positive, _positive_text(preset_id or "")))
            for style, pattern, _, _ in _STYLES
            if style not in excluded_styles
            and not (source_index and style in {"expressive", "technical"})
            and re.search(pattern, source)
        ),
        None,
    )
    for recipe_id, style, patterns in _RECIPE_RULES:
        if explicit_style and explicit_style not in _RECIPE_BASE_STYLES.get(recipe_id, set()):
            continue
        if all(re.search(pattern, positive) for pattern in patterns):
            return recipe(recipe_id, style)
    if explicit_style:
        return recipe(_STYLE_RECIPES[explicit_style], explicit_style)
    if re.search(r"без\s+засечек|sans[- ]serif", brief, re.I):
        return recipe("R01", "scandinavian")
    if re.search(r"кафе|cafe|coffee|кофе|книг|book", positive):
        return resolve("warm-editorial", "lora", "onest")
    if re.search(r"фитнес|fitness|спорт|sport", positive):
        return resolve("active-readable", "rubik", "onest")
    if re.search(r"финанс|finance|медицин|medical|образован|education", positive):
        return resolve("clear-trust", "golos-text", "golos-text")
    key = ("onest", "manrope", "golos-text")[
        int(hashlib.sha256(project_id.encode()).hexdigest(), 16) % 3
    ]
    return resolve("balanced", key, key)


def existing_typography_context(seed: str, files: dict[str, str]) -> str:
    """An edit/continuation follows accepted source, not a newly inferred brief.

    The pre-build contract is the final block appended by agent_preparation.
    Removing that first-build-only block also preserves legacy/custom designs.
    """
    if not {"typography.json", "DESIGN.md"}.intersection(files):
        return seed
    context = seed.partition("\n\nOMNIA DESIGN PRO · ")[0]
    selected = ""
    try:
        raw = json.loads(files.get("typography.json", "{}"))
        selected = json.dumps(
            {
                key: raw[key]
                for key in ("version", "heading", "body", "accent", "ui", "code", "numeric")
                if key in raw
            },
            ensure_ascii=False,
        )
    except (ValueError, KeyError, TypeError):
        pass
    return context + (
        "\n\nСуществующая типографика из исходников имеет приоритет: "
        + selected
        + ". Прочитай typography.json, DESIGN.md и текущий CSS; сохрани package/lock и шрифты "
        "при обычной правке или продолжении. Не пересеивай legacy-проект без manifest. "
        "Только явная просьба владельца изменить оформление разрешает согласованно обновить "
        "выбор, CSS, dependencies, lock и design memory."
    )


def seed_typography(files: dict[str, str], decision: TypographyDecision) -> dict[str, str]:
    """Initial seed only. Preserve existing source, including custom choices.

    Fontsource packages are dependency-free and script-free; their pnpm v9
    entries are inserted without resolving or changing any existing dependency.
    """
    if "typography.json" in files:
        return files
    required = {"package.json", "pnpm-lock.yaml", "src/app/globals.css"}
    if not required.issubset(files):
        raise ValueError("typography seed requires the complete MAX starter")
    result = dict(files)
    package = json.loads(files["package.json"])
    lock = files["pnpm-lock.yaml"]
    if (
        not lock.startswith("lockfileVersion: '9.0'")
        or "\npackages:\n" not in lock
        or "\nsnapshots:\n" not in lock
    ):
        raise ValueError("unsupported typography lockfile")
    font_faces: list[str] = []
    selected = {}
    for key in decision.families:
        row = font_catalogue()["fonts"][key]
        name, version = row["package"], row["version"]
        if name in package.get("dependencies", {}):
            raise ValueError("font dependency already present without a decision")
        package.setdefault("dependencies", {})[name] = version
        dependency = f"      '{name}':\n        specifier: {version}\n        version: {version}\n"
        lock = lock.replace("    dependencies:\n", "    dependencies:\n" + dependency, 1)
        lock = lock.replace(
            "\npackages:\n",
            f"\npackages:\n\n  '{name}@{version}':\n"
            f"    resolution: {{integrity: {row['integrity']}}}\n",
            1,
        )
        lock = lock.replace("\nsnapshots:\n", f"\nsnapshots:\n\n  '{name}@{version}': {{}}\n", 1)
        # Per-subset Fontsource CSS omits unicode-range. Importing several such
        # files lets the last (Latin) face replace Cyrillic. Emit the verified
        # ranges from the package's combined CSS and bundle only checked WOFF2.
        font_faces.extend(
            "@font-face {\n"
            f"  font-family: '{row['family']}';\n  font-style: normal;\n"
            f"  font-weight: {asset['weight']};\n  font-display: swap;\n"
            f"  src: url('~{name}/files/{filename}') format('woff2');\n"
            f"  unicode-range: {asset['unicode_range']};\n}}"
            for filename, asset in row["assets"].items()
        )
        selected[key] = {name: value for name, value in row.items() if name != "license_text"}
        result[f"public/fonts/licenses/{key}.txt"] = row["license_text"]
    heading_weights = next(
        (row["weights"] for row in selected.values() if row["family"] == decision.heading), [400]
    )
    heading_weight = 600 if 600 in heading_weights else 400
    role_variables = "".join(
        f"  --font-{role}: '{getattr(decision, role)}', {fallback};\n"
        for role, fallback in (
            ("accent", "sans-serif"),
            ("ui", "sans-serif"),
            ("code", "monospace"),
            ("numeric", "sans-serif"),
        )
        if getattr(decision, role)
    )
    role_rules = ""
    if decision.ui:
        role_rules += "button, input, select, textarea { font-family: var(--font-ui); }\n"
    if decision.code:
        role_rules += "code, pre, kbd, samp { font-family: var(--font-code); }\n"
    if decision.accent:
        role_rules += ".font-accent { font-family: var(--font-accent); }\n"
    numeric_family = "var(--font-numeric)" if decision.numeric else "var(--font-body)"
    role_rules += (
        f".font-numeric {{ font-family: {numeric_family}; font-variant-numeric: tabular-nums; }}\n"
    )
    css = "\n".join(font_faces) + (
        f"\n:root {{\n  --font-display: '{decision.heading}', sans-serif;\n"
        f"  --font-body: '{decision.body}', sans-serif;\n{role_variables}}}\n"
        "@layer base {\nbody { font-family: var(--font-body); font-synthesis: none; }\n"
        f"h1, h2, h3 {{ font-family: var(--font-display); font-weight: {heading_weight}; }}\n"
        f"{role_rules}}}\n"
    )
    # Both defaults and Tailwind preflight belong to base. Defaults must follow
    # preflight there, while utilities retain their higher cascade layer.
    globals_css, imports_updated = re.subn(
        r"(@import\s+[\"']tailwindcss[\"']\s*;)",
        '\\1\n@import "./typography.css";',
        files["src/app/globals.css"],
        count=1,
    )
    if imports_updated != 1:
        raise ValueError("typography seed requires the MAX Tailwind entrypoint")
    result.update(
        {
            "package.json": json.dumps(package, ensure_ascii=False, indent=2) + "\n",
            "pnpm-lock.yaml": lock,
            "typography.json": json.dumps(
                {**asdict(decision), "packages": selected}, ensure_ascii=False, indent=2
            )
            + "\n",
            "src/app/typography.css": css,
            "src/app/globals.css": globals_css,
        }
    )
    return result
