"""Unit tests for the SAST gate (services/sast_gate.py).

Pure module (re + dataclasses only) → no container, no settings, no DB. Covers
the high-confidence sinks + secret detection + the false-positive guards that
keep the gate from flooding the heal loop with noise.
"""

from __future__ import annotations

from omnia_api.services import sast_gate as sg


def test_clean_files_are_safe():
    files = {
        "src/app/page.tsx": "export default function P(){ return <div>hi</div> }",
        "src/lib/x.ts": "export const x = 1;",
    }
    v = sg.check_sast(files)
    assert v.safe is True and v.findings == []


def test_eval_is_code_injection():
    v = sg.check_sast({"src/app/a.ts": "const r = eval(userInput);"})
    assert v.safe is False
    assert any(f.cwe == "CWE-94" for f in v.findings)


def test_new_function_is_code_injection():
    v = sg.check_sast({"src/app/a.ts": "const f = new Function('a', body);"})
    assert any(f.cwe == "CWE-94" for f in v.findings)


def test_dangerously_set_inner_html_flagged():
    v = sg.check_sast({"src/app/a.tsx": "<div dangerouslySetInnerHTML={{__html: x}} />"})
    assert any(f.cwe == "CWE-79" for f in v.findings)


def test_sql_interpolation_is_injection():
    code = "const q = `SELECT * FROM users WHERE id = ${id}`;"
    v = sg.check_sast({"src/app/api/custom/r.ts": code})
    assert any(f.cwe == "CWE-89" for f in v.findings)


def test_python_subprocess_shell_true_is_command_injection():
    v = sg.check_sast({"app/main.py": "subprocess.run(cmd, shell=True)"})
    assert any(f.cwe == "CWE-78" for f in v.findings)


def test_python_sql_fstring_is_injection():
    v = sg.check_sast({"app/db.py": 'cur.execute(f"SELECT * FROM t WHERE id={id}")'})
    assert any(f.cwe == "CWE-89" for f in v.findings)


def test_hardcoded_aws_key_is_secret():
    v = sg.check_sast({"src/lib/c.ts": 'const k = "AKIAIOSFODNN7EXAMPLE";'})
    # The AKIA token-shape rule fires independently of the literal-assign filter.
    assert any(f.cwe == "CWE-798" for f in v.findings)


def test_hardcoded_password_literal_is_secret():
    v = sg.check_sast({"src/lib/c.ts": 'const password = "S3cr3tP@ssw0rd!";'})
    assert v.safe is False
    assert any(f.cwe == "CWE-798" for f in v.findings)


def test_private_key_in_source_is_secret():
    v = sg.check_sast({"src/lib/k.ts": "-----BEGIN RSA PRIVATE KEY-----\nMIIE..."})
    assert any(f.cwe == "CWE-798" for f in v.findings)


def test_env_reference_is_not_a_secret():
    """A password sourced from env is correct, not a finding (false-positive guard)."""
    v = sg.check_sast({"src/lib/c.ts": "const password = process.env.DB_PASSWORD;"})
    assert v.safe is True


def test_placeholder_value_is_not_a_secret():
    for val in ("your-api-key-here", "changeme", "xxxxxxxx", "example-secret", "<token>"):
        v = sg.check_sast({"src/lib/c.ts": f'const api_key = "{val}";'})
        assert v.safe is True, f"placeholder {val!r} must not trip the gate"


def test_non_source_files_are_ignored():
    # secrets in docs/json/lockfiles are out of scope for this writer-code gate
    v = sg.check_sast({"README.md": 'password = "S3cr3tP@ssw0rd!"'})
    assert v.safe is True


def test_finding_exposes_check_shape_for_feedback():
    """Finding has .ok/.name/.detail so it can feed agent_gate_feedback uniformly."""
    f = sg.scan_sast({"src/a.ts": "eval(x)"})[0]
    assert f.ok is False
    assert f.cwe in f.name and f.path in f.name
    assert f.detail == f.rule


def test_summary_lists_cwes_and_paths():
    v = sg.check_sast({"src/a.ts": "eval(x)", "app/b.py": "os.system(cmd)"})
    assert "CWE-94" in v.summary and "CWE-78" in v.summary
    assert "src/a.ts" in v.summary and "app/b.py" in v.summary
