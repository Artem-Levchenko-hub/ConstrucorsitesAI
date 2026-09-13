from __future__ import annotations

import logging
import re
from typing import Any

from omnia_api.core.config import (
    get_settings,
    model_for_role,
)
from omnia_api.core.redis import publish_event
from omnia_api.schemas.project import CONTAINER_BROWSER_TEMPLATES as CONTAINER_NEXT
from omnia_api.services.file_extractor import (
    UnsafePathError,
    extract_edits,
)
from omnia_api.services.generation.contracts import (
    GenerationIds,
    ProjectGenerationFacts,
    SourceBaseline,
)
from omnia_api.services.generation.file_transforms import (
    _extract_files_and_edits,
    _salvage_html,
    _text_preserved_ratio,
    _visible_words,
)
from omnia_api.services.generation.stream_attempt import ModelStream
from omnia_api.services.link_validator import repair_orphaned_anchors_inline
from omnia_api.services.llm_client import stream_chat_completion

_log = logging.getLogger("omnia_api.routers.messages")


async def retry_unmatched_or_conflicting_edits(
    *,
    accumulated: str,
    baseline: SourceBaseline,
    edit_conflicts: list[str],
    files: dict[str, str],
    force_model: str | None,
    stream: ModelStream,
    surgical: bool,
    usage_data: dict[str, Any] | None,
) -> tuple[dict[str, str], str, dict[str, Any] | None]:
    if surgical and not files and accumulated.strip():
        retry_note = (
            "Правка не применилась: SEARCH-блок не совпал с файлом побайтно "
            "или не нашёлся уникально. Повтори ответ — найди нужный фрагмент в "
            "показанном текущем файле, скопируй его в SEARCH ТОЧНО как есть (те "
            "же пробелы, переводы строк, кавычки) и добавь 1–2 соседние строки "
            "для уникальности. Только <edit>, меняй ровно запрошенное, остальное "
            "не трогай."
        )
        stream.messages.append({"role": "assistant", "content": accumulated})
        stream.messages.append({"role": "user", "content": retry_note})
        print("[PP] surgical_retry (no patch applied) -> re-ask", flush=True)
        # Escalate the retry to a stronger reasoning model — it reproduces
        # byte-exact SEARCH blocks far more reliably than the cheap edit model
        # (restores d61214b, clobbered by 2133cfd on a stale base).
        _esc_model = model_for_role("edit_escalation", override=force_model)
        await stream.run(_esc_model, force_single_shot=True, force_all=_esc_model)
        _retry_acc = str(stream.last_attempt.text)
        if _retry_acc.strip():
            accumulated = accumulated + "\n" + _retry_acc
            if stream.last_attempt.usage and isinstance(stream.last_attempt.usage, dict):
                usage_data = stream.last_attempt.usage
            try:
                files, _ = _extract_files_and_edits(_retry_acc, baseline.files)
            except (UnsafePathError, ValueError):
                files = {}
        print(f"[PP] surgical_retry applied files={len(files)}", flush=True)

    # --- Surgical conflict retry: PARTIAL apply -----------------------
    # Some SEARCH blocks matched, others didn't (a cheap model mis-copies one
    # long class line — e.g. drops a char in `min-h-[95vh]`). The applied edits
    # stay; the failed ones used to vanish silently (files non-empty → the
    # not-files retry above never fired), so the user saw part of the request
    # ignored. Re-ask ONCE for ONLY the conflicted edits, with the exact
    # conflict list + a "anchor on short text/id, not long class strings"
    # nudge, and merge the result on top of the already-patched file.
    if surgical and files and edit_conflicts and accumulated.strip():
        _base_after_first = {**baseline.files, **files}
        _conf_note = (
            "Часть правок НЕ применилась — SEARCH не совпал с файлом:\n"
            + "\n".join(f"— {c}" for c in edit_conflicts[:6])
            + "\n\nПовтори ТОЛЬКО непрошедшие правки. Якорь SEARCH бери КОРОТКИЙ "
            "и ПРОСТОЙ: видимый ТЕКСТ (слова заголовка/кнопки/цены), атрибут "
            '`id="…"` или одно уникальное слово — НЕ копируй длинные `class="…"` '
            "со [скобочными] значениями (`min-h-[95vh]` и т.п.): cheap-модель их "
            "портит и SEARCH промахивается. Скопируй фрагмент из показанного файла "
            "символ-в-символ. Только <edit>, ровно непрошедшее, остальное не трогай."
        )
        stream.messages.append({"role": "assistant", "content": accumulated})
        stream.messages.append({"role": "user", "content": _conf_note})
        print(
            f"[PP] surgical_conflict_retry conflicts={len(edit_conflicts)}",
            flush=True,
        )
        _esc_model = model_for_role("edit_escalation", override=force_model)
        await stream.run(_esc_model, force_single_shot=True, force_all=_esc_model)
        _cr_acc = str(stream.last_attempt.text)
        if _cr_acc.strip():
            try:
                _cr_files, _ = _extract_files_and_edits(_cr_acc, _base_after_first)
            except (UnsafePathError, ValueError):
                _cr_files = {}
            if _cr_files:
                files = {**files, **_cr_files}
                accumulated = accumulated + "\n" + _cr_acc
                if stream.last_attempt.usage and isinstance(stream.last_attempt.usage, dict):
                    usage_data = stream.last_attempt.usage
                print(
                    f"[PP] surgical_conflict_retry applied files={list(files.keys())}",
                    flush=True,
                )
            else:
                print("[PP] surgical_conflict_retry no_new_files", flush=True)

    return files, accumulated, usage_data


async def recover_selected_zone_edit(
    *,
    accumulated: str,
    baseline: SourceBaseline,
    files: dict[str, str],
    force_model: str | None,
    ids: GenerationIds,
    prompt_text: str,
    selected_elements: list[dict[str, Any]] | None,
    surgical: bool,
    usage_data: dict[str, Any] | None,
) -> tuple[dict[str, str], str, dict[str, Any] | None]:
    if surgical and not files and selected_elements and baseline.files.get("index.html"):
        from omnia_api.services import zone_edit as _ze
        from omnia_api.services.prompt_builder import build_zone_edit_messages

        _old_index_src = baseline.files["index.html"]
        _span = _ze.find_enclosing_block(_old_index_src, _ze.distinctive_anchors(selected_elements))
        if _span is not None:
            _block = _old_index_src[_span[0] : _span[1]]
            _z_notice = "\n\n*Меняю только выделенную зону, остальную страницу не трогаю…*\n\n"
            accumulated = accumulated + _z_notice
            await publish_event(
                ids.project_id,
                "llm.chunk",
                {"message_id": str(ids.assistant_message_id), "delta": _z_notice},
            )
            print(
                f"[PP] zone_edit start span={_span} block_len={len(_block)}",
                flush=True,
            )
            _z_parts: list[str] = []
            _z_usage: dict[str, Any] | None = None
            try:
                async for _ev in stream_chat_completion(
                    build_zone_edit_messages(_block, prompt_text, selected_elements),
                    model_for_role("freeform_writer", override=force_model),
                    str(ids.user_id),
                    str(ids.project_id),
                    str(ids.assistant_message_id),
                ):
                    if "delta" in _ev:
                        # Accumulate SILENTLY — the model streams a raw
                        # <section>, which is neither <file> nor <edit>, so
                        # publishing it would dump raw HTML into the chat. We
                        # add a clean "Правка" chip once it lands.
                        _z_parts.append(str(_ev["delta"]))
                    elif "usage" in _ev:
                        _z_usage = _ev["usage"]
            except Exception as _ze_exc:
                print(f"[PP] zone_edit stream_err {_ze_exc!r}", flush=True)
            _z_acc = "".join(_z_parts)
            _new_block = _ze.extract_block(_z_acc)
            _old_root_id = _ze.root_id(_block)
            # Accept only a real block whose root id matches (proves the model
            # returned the SAME zone rewritten, not a different/empty thing).
            if _new_block and (_old_root_id is None or _ze.root_id(_new_block) == _old_root_id):
                files = {"index.html": _ze.splice(_old_index_src, _span, _new_block)}
                if _z_usage:
                    usage_data = _z_usage
                # Clean chip in chat — never the raw <section>.
                accumulated = accumulated + (
                    '\n<edit path="index.html">\n'
                    "Обновил выделенную зону, остальную страницу не трогал.\n"
                    "</edit>\n"
                )
                print(
                    f"[PP] zone_edit applied new_block_len={len(_new_block)}",
                    flush=True,
                )
            else:
                print(
                    "[PP] zone_edit rejected (no/invalid block or id mismatch)",
                    flush=True,
                )

    return files, accumulated, usage_data


async def recover_html_edit(
    *,
    accumulated: str,
    baseline: SourceBaseline,
    files: dict[str, str],
    force_model: str | None,
    history_serialized: list[dict[str, str]],
    ids: GenerationIds,
    prompt_text: str,
    selected_elements: list[dict[str, Any]] | None,
    surgical: bool,
    usage_data: dict[str, Any] | None,
    stream: ModelStream,
) -> tuple[dict[str, str], str, dict[str, Any] | None]:
    if surgical and not files and baseline.files.get("index.html"):
        from omnia_api.services.prompt_builder import build_edit_rewrite_messages

        rw_msgs = build_edit_rewrite_messages(
            baseline.files, history_serialized, prompt_text, selected_elements
        )
        _rw_notice = (
            "\n\n*Точечно не вышло — переписываю страницу аккуратно, сохраняя остальное…*\n\n"
        )
        accumulated = accumulated + _rw_notice
        await publish_event(
            ids.project_id,
            "llm.chunk",
            {"message_id": str(ids.assistant_message_id), "delta": _rw_notice},
        )
        print("[PP] surgical_rewrite_fallback start", flush=True)
        _rw_parts: list[str] = []
        _rw_usage: dict[str, Any] | None = None
        try:
            # Use the reliable full-file writer (freeform_writer), not the
            # cheap edit model — the cheap model shadow-dropped the rewrite
            # (empty output) on prod. This pass runs only on the rare edit
            # that <edit> couldn't express, so the slightly richer model is
            # worth a dependable result.
            async for _ev in stream_chat_completion(
                rw_msgs,
                model_for_role("freeform_writer", override=force_model),
                str(ids.user_id),
                str(ids.project_id),
                str(ids.assistant_message_id),
            ):
                if "delta" in _ev:
                    _d = str(_ev["delta"])
                    _rw_parts.append(_d)
                    stream.pub.seq = int(stream.pub.seq) + 1
                    stream.pub.content = str(stream.pub.content) + _d
                    await publish_event(
                        ids.project_id,
                        "llm.chunk",
                        {
                            "message_id": str(ids.assistant_message_id),
                            "delta": _d,
                            "seq": int(stream.pub.seq),
                        },
                    )
                elif "usage" in _ev:
                    _rw_usage = _ev["usage"]
        except Exception as _rw_exc:
            print(f"[PP] surgical_rewrite_fallback stream_err {_rw_exc!r}", flush=True)
        _rw_acc = "".join(_rw_parts)
        accumulated = accumulated + _rw_acc
        try:
            _rw_files, _ = _extract_files_and_edits(_rw_acc, baseline.files)
        except (UnsafePathError, ValueError):
            _rw_files = {}
        _old_index = baseline.files.get("index.html", "")
        _new_index = _rw_files.get("index.html", "")
        # The writer sometimes streams raw HTML without the <file> wrapper —
        # salvage it instead of dropping the whole edit.
        if not _new_index:
            _salvaged = _salvage_html(_rw_acc)
            if _salvaged:
                _new_index = _salvaged
                _rw_files = {"index.html": _salvaged}
                print(
                    f"[PP] surgical_rewrite_fallback salvaged_html len={len(_salvaged)}",
                    flush=True,
                )
        _ratio = _text_preserved_ratio(_old_index, _new_index) if _new_index else 0.0
        # ≥0.6 of the original words must survive — a real scoped edit keeps
        # the copy; a re-design replaces it. Reject the drift, keep the page.
        if _new_index and _ratio >= 0.6:
            files = _rw_files
            if _rw_usage:
                usage_data = _rw_usage
            print(
                f"[PP] surgical_rewrite_fallback applied ratio={_ratio:.2f}",
                flush=True,
            )
        else:
            print(
                f"[PP] surgical_rewrite_fallback rejected ratio={_ratio:.2f} "
                f"new_len={len(_new_index)}",
                flush=True,
            )

    return files, accumulated, usage_data


def bind_interactive_edit(
    *,
    _is_code: bool,
    baseline: SourceBaseline,
    files: dict[str, str],
    project_info: ProjectGenerationFacts,
    surgical: bool,
) -> dict[str, str]:
    if surgical and files and project_info.template not in CONTAINER_NEXT and not _is_code:
        _before = files
        files = repair_orphaned_anchors_inline(baseline.files, files)
        if files != _before:
            print("[PP] orphaned_anchors repaired", flush=True)

    return files


async def recover_container_edit(
    *,
    accumulated: str,
    baseline: SourceBaseline,
    files: dict[str, str],
    force_model: str | None,
    history_serialized: list[dict[str, str]],
    ids: GenerationIds,
    project_info: ProjectGenerationFacts,
    prompt_text: str,
    selected_elements: list[dict[str, Any]] | None,
    surgical: bool,
    usage_data: dict[str, Any] | None,
    stream: ModelStream,
) -> tuple[dict[str, str], str, dict[str, Any] | None]:
    if (
        surgical
        and not files
        and get_settings().use_container_edit_rewrite
        and project_info.template in CONTAINER_NEXT
        and baseline.files
    ):
        from omnia_api.services.prompt_builder import build_container_rewrite_messages

        # Pick targets: the path(s) the failed <edit> named, then any path the
        # prompt mentions ("Файл: src/…" — the «Починить» card includes it),
        # restricted to files we actually have. Cap to a few so we never
        # blind-rewrite the whole app.
        _targets: dict[str, str] = {}
        try:
            for _p in extract_edits(accumulated):
                if _p in baseline.files:
                    _targets[_p] = baseline.files[_p]
        except (UnsafePathError, ValueError):
            pass
        for _m in re.finditer(r"(?:Файл|file)\s*[:=]\s*([^\s,;]+)", prompt_text, re.I):
            _cand = _m.group(1).strip().strip("`\"'.")
            if _cand in baseline.files and _cand not in _targets:
                _targets[_cand] = baseline.files[_cand]
        if not _targets:
            for _cand in ("src/app/page.tsx", "src/App.tsx", "app/page.tsx"):
                if _cand in baseline.files:
                    _targets[_cand] = baseline.files[_cand]
                    break
        _targets = dict(list(_targets.items())[:4])

        if _targets:
            _crc_notice = (
                "\n\n*Точечно не вышло — переписываю нужные файлы аккуратно, "
                "сохраняя остальное…*\n\n"
            )
            accumulated = accumulated + _crc_notice
            await publish_event(
                ids.project_id,
                "llm.chunk",
                {"message_id": str(ids.assistant_message_id), "delta": _crc_notice},
            )
            print(
                f"[PP] container_rewrite_fallback start targets={list(_targets)}",
                flush=True,
            )
            _crc_parts: list[str] = []
            _crc_usage: dict[str, Any] | None = None
            try:
                async for _ev in stream_chat_completion(
                    build_container_rewrite_messages(
                        _targets,
                        history_serialized,
                        prompt_text + project_info.restoration_context,
                        selected_elements,
                        template=project_info.template,
                    ),
                    model_for_role("freeform_writer", override=force_model),
                    str(ids.user_id),
                    str(ids.project_id),
                    str(ids.assistant_message_id),
                ):
                    if "delta" in _ev:
                        _d = str(_ev["delta"])
                        _crc_parts.append(_d)
                        stream.pub.seq = int(stream.pub.seq) + 1
                        stream.pub.content = str(stream.pub.content) + _d
                        await publish_event(
                            ids.project_id,
                            "llm.chunk",
                            {
                                "message_id": str(ids.assistant_message_id),
                                "delta": _d,
                                "seq": int(stream.pub.seq),
                            },
                        )
                    elif "usage" in _ev:
                        _crc_usage = _ev["usage"]
            except Exception as _crc_exc:
                print(
                    f"[PP] container_rewrite_fallback stream_err {_crc_exc!r}",
                    flush=True,
                )
            _crc_acc = "".join(_crc_parts)
            accumulated = accumulated + _crc_acc
            try:
                _crc_files, _ = _extract_files_and_edits(_crc_acc, baseline.files)
            except (UnsafePathError, ValueError):
                _crc_files = {}
            # Accept only files we asked for, and only when real content
            # survived: a scoped fix keeps the file's text; a redesign
            # replaces it. Code-heavy files (few visible words) bypass the
            # ratio — there the word-overlap metric is noise.
            _accepted: dict[str, str] = {}
            for _p, _new_content in _crc_files.items():
                if _p not in _targets:
                    continue
                _old_content = _targets[_p]
                _ratio = _text_preserved_ratio(_old_content, _new_content)
                _few_words = len(_visible_words(_old_content)) < 15
                if (
                    _new_content.strip()
                    and len(_new_content) > 80
                    and (_ratio >= 0.45 or _few_words or len(_old_content) < 400)
                ):
                    _accepted[_p] = _new_content
                    print(
                        f"[PP] container_rewrite accept {_p} ratio={_ratio:.2f}",
                        flush=True,
                    )
                else:
                    print(
                        f"[PP] container_rewrite reject {_p} ratio={_ratio:.2f} "
                        f"new_len={len(_new_content)}",
                        flush=True,
                    )
            if _accepted:
                files = _accepted
                if _crc_usage:
                    usage_data = _crc_usage

    return files, accumulated, usage_data
