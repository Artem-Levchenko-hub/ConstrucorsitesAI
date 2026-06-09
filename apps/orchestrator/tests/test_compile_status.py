"""Unit tests for Next.js/Turbopack compile-error detection."""

from __future__ import annotations

from omnia_orchestrator.services.compile_status import parse_next_compile_error

# Turbopack's error-line glyph (U+2A2F), built via chr() so the literal doesn't
# trip ambiguous-unicode linting.
_X = chr(0x2A2F)

# A real-ish Turbopack failure block.
_TURBOPACK_FAIL = f"""\
   ▲ Next.js 15.1.0 (Turbopack)
   - Local:   http://localhost:3000
 ○ Compiling /(app) ...
 {_X} ./src/app/(app)/page.tsx:12:3
Ecmascript file had an error
  10 |   return (
  11 |     <main>
> 12 |   <Broken
     |   ^^^^^^^
Expression expected
 GET /(app) 500 in 240ms
"""

_MODULE_NOT_FOUND = f"""\
 ○ Compiling /dashboard ...
 {_X} ./src/app/(app)/dashboard/page.tsx
Module not found: Can't resolve '@/lib/widgets'
  1 | import {{ Widget }} from '@/lib/widgets'
"""

_CLEAN = """\
   ▲ Next.js 15.1.0 (Turbopack)
 ○ Compiling /(app) ...
 ✓ Compiled /(app) in 1820ms
 GET /(app) 200 in 2100ms
 GET /api/health 200 in 12ms
"""

_RECOVERED = f"""\
 {_X} ./src/app/(app)/page.tsx:5:1
Module not found: Can't resolve '@/lib/oops'
 ○ Compiling /(app) ...
 ✓ Compiled /(app) in 900ms
 GET /(app) 200 in 30ms
"""


def test_clean_logs_report_ok() -> None:
    ok, error, file = parse_next_compile_error(_CLEAN)
    assert ok is True
    assert error is None
    assert file is None


def test_empty_logs_report_ok() -> None:
    assert parse_next_compile_error("") == (True, None, None)


def test_turbopack_ecmascript_error_detected() -> None:
    ok, error, file = parse_next_compile_error(_TURBOPACK_FAIL)
    assert ok is False
    assert error is not None
    assert "Ecmascript file had an error" in error
    assert file == "src/app/(app)/page.tsx"


def test_module_not_found_detected_with_file() -> None:
    ok, error, file = parse_next_compile_error(_MODULE_NOT_FOUND)
    assert ok is False
    assert "Module not found" in (error or "")
    assert file == "src/app/(app)/dashboard/page.tsx"


def test_failure_then_successful_recompile_is_ok() -> None:
    # HMR recovery: a later success supersedes the earlier error.
    ok, error, _file = parse_next_compile_error(_RECOVERED)
    assert ok is True
    assert error is None


def test_ansi_is_stripped_and_error_still_detected() -> None:
    colored = f"\x1b[31m {_X} ./src/app/page.tsx\x1b[0m\nModule not found: x\n"
    ok, error, file = parse_next_compile_error(colored)
    assert ok is False
    assert "\x1b" not in (error or "")
    assert file == "src/app/page.tsx"


def test_detail_is_bounded() -> None:
    noisy = f" {_X} ./src/app/page.tsx\n" + ("Module not found x\n" * 200)
    _ok, error, _file = parse_next_compile_error(noisy)
    assert error is not None
    assert len(error) <= 600


def test_node_modules_paths_are_not_picked_as_file() -> None:
    logs = f" {_X} Module not found\n  at /app/node_modules/next/dist/foo.js\n"
    ok, _error, file = parse_next_compile_error(logs)
    assert ok is False
    assert file is None  # node_modules is out of scope; only project src/app/…
