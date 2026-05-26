"""JSON syntax + shape audit for all golden specs under ``tests/golden/``.

Each ``*.json`` under ``tests/golden/`` is a regression seed for a particular
prompt class. This test does NOT exercise the generator — it just guarantees
the spec files themselves remain valid + structurally sound, so future audit
runs (`test_golden_against_generation_output`, Phase H re-baseline) won't trip
on a typo'd HEX or a renamed key.

Schema reference: ``apps/api/tests/golden/apteka.json`` (canonical example).
"""

from __future__ import annotations

import json
from pathlib import Path

GOLDEN_DIR = Path(__file__).parent / "golden"


def test_all_goldens_parse() -> None:
    """Every golden spec file must be valid JSON with the required shape."""
    golden_files = sorted(GOLDEN_DIR.glob("*.json"))
    assert golden_files, "no golden specs found under tests/golden/*.json"

    for golden_file in golden_files:
        with open(golden_file, encoding="utf-8") as fh:
            g = json.load(fh)

        # Top-level required fields
        assert g["id"] == golden_file.stem, (
            f"{golden_file.name}: id={g.get('id')!r} != filename stem"
        )
        assert g["version"] == 1, f"{golden_file.name}: version must be 1"
        assert g["input"]["template"] == "landing", (
            f"{golden_file.name}: only 'landing' template supported in MVP goldens"
        )
        assert g["input"].get("prompt"), f"{golden_file.name}: missing input.prompt"
        assert g["input"].get("project_name"), (
            f"{golden_file.name}: missing input.project_name"
        )

        # expected block
        exp = g["expected"]
        assert "preset_id_candidates" in exp, (
            f"{golden_file.name}: missing expected.preset_id_candidates"
        )
        assert exp["preset_id_candidates"], (
            f"{golden_file.name}: preset_id_candidates must be non-empty"
        )
        assert "palette_range" in exp, (
            f"{golden_file.name}: missing expected.palette_range"
        )
        assert "sections_present_min" in exp, (
            f"{golden_file.name}: missing expected.sections_present_min"
        )
        assert "fonts_must_be_in" in exp, (
            f"{golden_file.name}: missing expected.fonts_must_be_in"
        )

        # palette_range must have BOTH must_include and must_avoid
        pr = exp["palette_range"]
        assert pr["must_include_any_of"], (
            f"{golden_file.name}: palette_range.must_include_any_of empty"
        )
        assert pr["must_avoid_any_of"], (
            f"{golden_file.name}: palette_range.must_avoid_any_of empty"
        )

        # fonts_must_be_in must be a list of [display, body] pairs
        for pairing in exp["fonts_must_be_in"]:
            assert isinstance(pairing, list) and len(pairing) == 2, (
                f"{golden_file.name}: fonts_must_be_in entries must be "
                f"[display, body] pairs, got {pairing!r}"
            )
