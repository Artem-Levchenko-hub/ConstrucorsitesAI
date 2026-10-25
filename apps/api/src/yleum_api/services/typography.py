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

    @property
    def dependencies(self) -> dict[str, str]:
        fonts = font_catalogue()["fonts"]
        return {fonts[key]["package"]: fonts[key]["version"] for key in self.families}

    def prompt(self) -> str:
        custom = (
            f"Запрошен внешний шрифт {self.custom_request!r}; он НЕ установлен. "
            "Сохрани запрос; используй system fallback до доступного разрешённого asset. "
            "Не выдумывай загрузку файла и не скачивай произвольный URL."
            if self.custom_request
            else "Локальные CSS/assets уже закреплены в package.json и pnpm-lock.yaml."
        )
        return (
            f"TypographyDecision v{self.version}: heading — {self.heading}; body — {self.body}; "
            f"style — {self.style}. {custom} "
            "Источник истины: typography.json и src/app/typography.css. "
            "Не добавляй Google Fonts/CDN/next/font/google. "
            "При обычной правке сохрани существующий выбор; меняй только по просьбе владельца. "
            "Другие законно доступные шрифты владельца разрешены."
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
    ("technical", r"футур|futur|techno", "exo-2", "onest"),
)


def _positive_text(text: str) -> str:
    # Keep offsets, so role labels and custom spelling still refer to the brief.
    return _NEGATION.sub(lambda m: " " * len(m[0]), text).lower()


_ROLE_LABEL = r"\b(?:заголов\w*|heading|display|основн\w*|текст\w*|body|paragraph)\b"


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
    return "heading" if re.match(r"заголов|heading|display", role) else "body"


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
    excluded.update(heading for style, _, heading, _ in _STYLES if style in excluded_styles)
    safe = next(
        (key for key in ("onest", "manrope", "golos-text", "rubik") if key not in excluded), None
    )

    def resolve(
        style: str, heading: str | None, body: str | None, custom: str | None = None
    ) -> TypographyDecision:
        heading = safe if heading in excluded else heading
        body = safe if body in excluded else body
        return TypographyDecision(
            1,
            style,
            fonts[heading]["family"] if heading else "system-ui",
            fonts[body]["family"] if body else "system-ui",
            tuple(dict.fromkeys(key for key in (heading, body) if key)),
            custom,
        )

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
                    if heading in {None, "lora", "prata", "unbounded", "exo-2"}
                    else heading
                )
            )
        )
        return resolve("owner-custom-pending" if custom else "owner-font", heading, body, custom)
    if re.search(r"без\s+засечек|sans[- ]serif", brief, re.I):
        return resolve("scandinavian", "manrope", "manrope")
    for source_index, source in enumerate((positive, _positive_text(preset_id or ""))):
        for style, pattern, heading, body in _STYLES:
            if style in excluded_styles or (source_index and style in {"expressive", "technical"}):
                continue
            if re.search(pattern, source):
                return resolve(style, heading, body)
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
            {key: raw[key] for key in ("version", "heading", "body")},
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
    imports: list[str] = []
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
        imports.extend(f'@import "{path}";' for path in row["imports"])
        selected[key] = {name: value for name, value in row.items() if name != "license_text"}
        result[f"public/fonts/licenses/{key}.txt"] = row["license_text"]
    heading_weights = (
        font_catalogue()["fonts"][decision.families[0]]["weights"] if decision.families else [400]
    )
    heading_weight = 600 if 600 in heading_weights else 400
    css = "\n".join(imports) + (
        f"\n:root {{\n  --font-display: '{decision.heading}', sans-serif;\n"
        f"  --font-body: '{decision.body}', sans-serif;\n}}\n"
        "@layer base {\nbody { font-family: var(--font-body); font-synthesis: none; }\n"
        f"h1, h2, h3 {{ font-family: var(--font-display); font-weight: {heading_weight}; }}\n}}\n"
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
