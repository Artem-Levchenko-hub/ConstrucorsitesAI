"""Sol's completion declaration on the MAX text-action path."""
from __future__ import annotations

import re
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict

from yleum_api.services.agent_progress import bounded_redacted_diagnostic
from yleum_api.services.generation.continuation import source_fingerprint

SOL_COMPLETION_REPORT = (
    'Finish the original request in this workspace, including its later screens and data flows. '
    'Before done, walk through every requested user action against the current source. '
    'A placeholder, an unreachable screen or an unconnected handler is still outstanding. '
    'Include both "summary" and "remaining_work" in its JSON. In remaining_work give a '
    'string or an array of strings listing missing requested behavior. Use "" or [] when all '
    'behavior is implemented. List unverified checks separately in summary.'
)
_REPORT_MISSING = "Check the original request and list any unfinished features in remaining_work."
# Older text replies and contradictory summaries can explicitly disclose unfinished
# features. A missing device/browser check alone is not an unfinished feature.
_UNFINISHED = re.compile(
    r"\b(?:не (?:реализован[аоы]?|доделан[аоы]?)\b|"
    r"(?:not (?:yet )?implemented|remain(?:s)? unimplemented)\b|"
    r"осталось (?:реализовать|доделать|добавить)\b)",
    re.IGNORECASE,
)
_QUOTED_COPY = re.compile(r'"[^"]*"|«[^»]*»|“[^”]*”|`[^`]*`')
_NON_DECLARATIVE = re.compile(
    r"\b(?:если|когда|if|when|unless|ничего|никак(?:ие|их)|ни одн\w*|none|nothing)\b|"
    r"\bнет\s+[^:;.!?]*\bкотор\w*\b|"
    r"\bno\s+[^.,:;!?]+?\s+(?:remain(?:s)?|that|which|are|is)\b",
    re.IGNORECASE,
)


def reported_omission(text: str) -> str:
    omitted = []
    for sentence in re.split(r"(?<=[.!?;])\s+|\n+", text):
        assertion = _QUOTED_COPY.sub("", sentence).strip()
        if (
            _UNFINISHED.search(assertion) and not assertion.endswith("?")
            and not _NON_DECLARATIVE.search(assertion)
        ):
            omitted.append(sentence.strip())
    return " ".join(omitted)


def _remaining_work(value: object) -> str | None:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value):
        return "\n".join(item.strip() for item in value)
    return None


class SolProductWork(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    detail: str | None = None
    source_at_report: str | None = None

    def feedback(
        self, args: Mapping[str, object], files: Mapping[str, str], *,
        requested: bool, preamble: str,
    ) -> str:
        report = _remaining_work(args.get("remaining_work"))
        detail = report or ""
        summary = args.get("summary")
        if not detail:
            narration = preamble + "\n" + (summary if isinstance(summary, str) else "")
            detail = reported_omission(narration)
        if detail:
            self.detail = bounded_redacted_diagnostic(detail, max_bytes=1800)
            self.source_at_report = product_source_fingerprint(files)
        elif self.source_at_report is None or (
            self.source_at_report != product_source_fingerprint(files)
        ):
            if (requested or self.detail) and report is None:
                self.detail = self.detail or _REPORT_MISSING
                return self.detail
            return ""
        return (
            "Continue in the same workspace from the saved code. Implement the missing "
            "behavior below, build the changed source and report the remaining work again.\n"
            + (self.detail or "")
        )


def product_source_fingerprint(files: Mapping[str, str]) -> str:
    from yleum_api.services.generation.agent_finalization import _is_edit_source_path

    return source_fingerprint({
        path: content for path, content in files.items()
        if _is_edit_source_path(path) and not path.startswith(("test/", "tests/", "e2e/"))
        and "/__tests__/" not in path and not re.search(r"\.(?:test|spec)\.[^.]+$", path)
    })
