"""Assignment redaction must remain safe on adversarial tool output."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from yleum_api.services import agent_progress
from yleum_api.services.agent_progress import (
    REDACTED,
    bounded_redacted_diagnostic,
    bounded_redacted_text,
    redact_sensitive_text,
)


@pytest.mark.parametrize("name", [
    "DATABASE_URL", "AUTH_SECRET", "token", "PASSWORD", "FTP_PASS",
    "PRIVATE_KEY", "PREFIX_ACCESS_KEY_SUFFIX", "API_KEY", "SERVICE_DSN",
    "PREFIX_DATABASE_URL", "APİ_KEY", "ſECRET",
])
@pytest.mark.parametrize("template", [
    "export {name}=hidden-value", '{{"{name}": "hidden-value"}}',
    "{{'{name}': 'hidden-value'}}", "{{{name}: 'hidden-value'}}",
])
def test_assignment_names_remain_redacted(name: str, template: str) -> None:
    source = template.format(name=name)
    assert redact_sensitive_text(source) == source.replace("hidden-value", REDACTED)


def test_nonsecret_fields_do_not_hide_nested_secret_labels() -> None:
    source = '''{"description": "TOKEN: 'hidden-value'", "ordinary": "keep"}'''
    assert redact_sensitive_text(source) == source.replace("hidden-value", REDACTED)


def test_multiple_assignments_keep_quotes_and_safe_fields() -> None:
    source = '''{"TOKEN": "first-value", ordinary: "keep", 'API_KEY': 'last-value'}'''
    assert redact_sensitive_text(source) == (
        source.replace("first-value", REDACTED).replace("last-value", REDACTED)
    )


@pytest.mark.parametrize("quote", ['"', "'"])
def test_partial_assignment_crossing_preview_boundary_is_redacted(quote: str) -> None:
    prefix = "safe " * 8 + f"TOKEN: {quote}"
    result = bounded_redacted_text(prefix + "credential-fragment" * 100, max_bytes=80,
                                   lookahead_bytes=16)
    assert result == prefix + REDACTED
    assert "credential" not in result


def test_diagnostic_redacts_full_assignment_before_selecting_head_and_tail() -> None:
    source = "safe head\n" * 100 + 'TOKEN: "' + "Q" * 400 + '"\n' + "safe tail\n" * 100
    result = bounded_redacted_diagnostic(source, max_bytes=200)
    assert "Q" not in result
    assert result.startswith("safe head")
    assert result.endswith("safe tail\n")
    assert "[output omitted]" in result


def test_redaction_adversarial_inputs_finish_in_bounded_subprocess() -> None:
    # A regression must time out a disposable child, never wedge the pytest process.
    program = r'''
import importlib.util, json, sys, time
from pathlib import Path
def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
progress = load(Path(sys.argv[1]), "progress")
provider = load(Path(sys.argv[1]).with_name("secret_safety.py"), "provider")
started = time.perf_counter()
cases = 0
for size in (16000, 32000):
    for value in ("x" * size, "TOKEN" * (size // 5), "\n" * size,
                  "name" + " " * size, '"' + "x" * size + '": "safe"'):
        assert progress.redact_sensitive_text(value) == value
        assert progress.bounded_redacted_text(value, max_bytes=4096) == value[:4096]
        assert provider.redact_provider_secrets(value) == value
        assert not provider.contains_provider_secret(value)
        cases += 1
    name = "x" * size + "TOKEN" + "y" * size
    assert progress.redact_sensitive_text(name + '=hidden') == name + '=[REDACTED]'
    assert progress.redact_sensitive_text('"' + name + '": "hidden"').endswith(
        ': "[REDACTED]"')
    assert progress.bounded_redacted_text('TOKEN: "' + "q" * size,
                                         max_bytes=4096) == 'TOKEN: "[REDACTED]'
    cases += 3
print(json.dumps({"cases": cases, "seconds": time.perf_counter() - started}))
'''
    result = subprocess.run(
        [sys.executable, "-B", "-c", program, str(Path(agent_progress.__file__))],
        capture_output=True, text=True, check=True, timeout=10,
    )
    assert json.loads(result.stdout)["cases"] == 16
