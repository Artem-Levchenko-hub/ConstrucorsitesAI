"""Pure explicit-intent detection and snapshot-safe owner font materialization."""

from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from yleum_api.core.errors import ApiError


@lru_cache(maxsize=1)
def _free_family_names() -> tuple[str, ...]:
    source = Path(__file__).parents[3] / "skills/ui-ux-pro-max/data/google-font-licenses.json"
    data = json.loads(source.read_text(encoding="utf-8"))
    return tuple(sorted((row["name"] for row in data["families"]), key=len, reverse=True))


def requested_font_names(prompt: str) -> list[tuple[str, str]]:
    from yleum_api.services.typography import _positive_text

    positive = _positive_text(prompt)
    families = _free_family_names()
    if positive.strip().strip('"«»') in {name.lower() for name in families}:
        return [(prompt.strip().strip('"«»'), "all")]
    prefix = re.compile(
        r"(?:\b(?:используй\w*|поставь|подключи\w*|примени\w*|замени\w*|"
        r"измени\w*|добавь|use|set|add|change)\s+(?:(?:текущий|основной|the)\s+)?"
        r"(?:шрифт\w*|font)\s+(?:(?:на|to)\s+)?|"
        r'(?:^|[,;.\n])\s*(?:шрифт|font)\s+|\b(?:шрифт|font)\s+(?=["«]))',
        re.I,
    )
    found = []
    for match in prefix.finditer(positive):
        start = match.end()
        quoted = re.match(r'["«]([^"»\n]{1,100})["»]', prompt[start:])
        url = re.match(r"https://[^\s,;]+", prompt[start:])
        if quoted:
            name, end = quoted[1], start + quoted.end()
        elif url:
            name, end = url[0], start + url.end()
        else:
            name = next(
                (
                    family
                    for family in families
                    if positive.startswith(family.lower(), start)
                    and (
                        start + len(family) == len(positive)
                        or not positive[start + len(family)].isalnum()
                    )
                ),
                "",
            )
            if name:
                end = start + len(name)
            else:
                unknown = re.match(r"[A-Za-z0-9]+(?:[ -][A-Za-z0-9]+){0,6}", prompt[start:])
                if unknown is None:
                    continue
                name = re.split(r"\s+(?:for|as|in)\s+", unknown[0], flags=re.I)[0]
                end = start + len(name)
        tail = positive[end : end + 60]
        role = (
            "heading"
            if re.match(r"\s*(?:(?:для|for)\s+)?(?:заголов|heading|display)", tail)
            else "body"
            if re.match(r"\s*(?:(?:для|for)\s+)?(?:текста|body|основного)", tail)
            else "all"
        )
        if (name, role) not in found:
            found.append((name, role))
    return found[:6]


def apply_owner_fonts(
    files: dict[str, str],
    fonts: list[tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, str]:
    if not fonts:
        return files
    entry = "src/app/globals.css"
    if entry not in files:
        raise ApiError(
            "font_target_unavailable",
            "В приложении не найден основной CSS для подключения шрифта",
            422,
        )
    out = dict(files)
    try:
        manifest = json.loads(files.get("typography.json", "{}"))
    except (ValueError, TypeError):
        raise ApiError("validation_failed", "Повреждён manifest типографики", 422) from None
    if not isinstance(manifest, dict):
        raise ApiError("validation_failed", "Повреждён manifest типографики", 422)
    roles = dict(manifest.get("owner_roles", {}))
    assets = dict(manifest.get("owner_assets", {}))
    imports: set[str] = set()
    group_digests: dict[tuple[str, str], str] = {}
    for binding, _ in fonts:
        key = (binding["family"].casefold(), binding["role"])
        group = sorted(
            {
                item["sha256"]
                for item, _ in fonts
                if (item["family"].casefold(), item["role"]) == key
            }
        )
        group_digests[key] = hashlib.sha256("|".join(group).encode()).hexdigest()[:20]
    checked_faces: dict[tuple[str, str, str, str], str] = {}
    for binding, bundle in sorted(fonts, key=lambda pair: pair[0]["role"] != "all"):
        family = binding["family"]
        alias = "yleum-owner-" + group_digests[(family.casefold(), binding["role"])]
        digest = binding["sha256"]
        role = binding["role"]
        selected_roles = ("heading", "body", "ui") if role == "all" else (role,)
        for selected in selected_roles:
            roles[selected] = {"family": family, "alias": alias}
            manifest[selected] = family
        for face in bundle["faces"]:
            # Opaque source files are independently addressable text snapshots.
            payload = face["data"]
            face_key = (alias, str(face["weight"]), face["style"], face["unicode_range"])
            if face_key in checked_faces and checked_faces[face_key] != payload:
                raise ApiError(
                    "validation_failed",
                    "Для одной роли выбраны разные версии одного начертания",
                    422,
                )
            checked_faces[face_key] = payload
            face_digest = hashlib.sha256((alias + payload).encode()).hexdigest()
            path = f"src/app/fonts/{face_digest}.css"
            css = (
                f'@font-face {{font-family:"{alias}";font-style:{face["style"]};'
                f"font-weight:{face['weight']};font-display:swap;"
                f'src:url(data:font/woff2;base64,{payload}) format("woff2");'
                f"unicode-range:{face['unicode_range']};}}\n"
            )
            if len(css.encode()) > 2 * 1024 * 1024:
                raise ApiError("validation_failed", "Слишком большой font asset", 422)
            out[path] = css
            imports.add(f'@import "./fonts/{face_digest}.css";')
        assets[digest] = {
            "family": family,
            "sha256": digest,
            "source": binding.get("source", "upload"),
        }
        if bundle.get("license_text"):
            out[f"public/fonts/licenses/owner-{digest}.txt"] = bundle["license_text"]
    old_css = out.get("src/app/typography.css", "")
    # Replace just our deterministic block. Preserve existing Fontsource and
    # arbitrary owner CSS; reapplying the same pin cannot grow imports/overrides.
    old_css = old_css.partition("/* YLEUM OWNER FONTS */")[0].rstrip()
    old_imports = set(re.findall(r'^@import "\./fonts/[a-f0-9]+\.css";$', old_css, re.M))
    old_css = re.sub(r'^@import "\./fonts/[a-f0-9]+\.css";\n?', "", old_css, flags=re.M)
    css = "\n".join(sorted(imports | old_imports)) + "\n" + old_css
    css += "\n/* YLEUM OWNER FONTS */\n:root {\n"
    variables = {
        "heading": "--font-display",
        "body": "--font-body",
        "ui": "--font-ui",
        "accent": "--font-accent",
        "code": "--font-code",
    }
    for role, selected in sorted(roles.items()):
        css += f'{variables[role]}: "{selected["alias"]}", sans-serif;\n'
    css += "}\n"
    selectors = {
        "heading": "h1,h2,h3,h4,h5,h6",
        "body": "body,p,li,blockquote",
        "ui": "button,input,select,textarea,label",
        "accent": '[data-font-role="accent"],.font-accent',
        "code": "code,pre,kbd,samp",
    }
    for role in sorted(roles):
        css += f"{selectors[role]} {{font-family:var({variables[role]}) !important;}}\n"
    out["src/app/typography.css"] = css
    if not re.search(r'@import\s+[\'"]\./typography\.css[\'"]', out[entry]):
        out[entry] = '@import "./typography.css";\n' + out[entry]
    manifest.update(version=2, owner_roles=roles, owner_assets=assets)
    manifest.update(style="owner-font", custom_request=None)
    out["typography.json"] = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    design = out.get("DESIGN.md", "")
    design = re.sub(
        r"\n\n## Typography decision\n.*?<!-- typography-decision-v1\n.*?\n-->\n?",
        "",
        design,
        flags=re.S,
    )
    owner_memory = (
        "<!-- yleum-owner-typography:start -->\n## Owner typography\n\n"
        "Явный выбор владельца уже подключён в src/app/typography.css. "
        "Сохранять при обычных правках; файлы fonts/*.css — opaque assets.\n\n"
        + json.dumps(roles, ensure_ascii=False, indent=2)
        + "\n<!-- yleum-owner-typography:end -->"
    )
    pattern = r"<!-- yleum-owner-typography:start -->.*?<!-- yleum-owner-typography:end -->"
    if re.search(pattern, design, re.S):
        out["DESIGN.md"] = re.sub(pattern, lambda _: owner_memory, design, flags=re.S)
    else:
        out["DESIGN.md"] = design.rstrip() + "\n\n" + owner_memory + "\n"
    if sum(len(value.encode()) for value in out.values()) > 60 * 1024 * 1024:
        raise ApiError(
            "validation_failed", "Недостаточно места в исходниках приложения для шрифтов", 422
        )
    return out
