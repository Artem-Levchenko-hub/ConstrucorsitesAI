"""Tests for the missing-ui-component shim guard (build-break safety net)."""

from __future__ import annotations

from omnia_api.services.ui_shims import ensure_ui_shims


def _page(imp: str) -> dict[str, str]:
    return {
        "src/app/(app)/dashboard/menu/page.tsx": (
            '"use client";\n'
            f'import {{ X }} from "{imp}";\n'
            "export default function P() { return null; }\n"
        )
    }


def test_missing_radio_group_gets_a_shim():
    files, injected, missing = ensure_ui_shims(_page("@/components/ui/radio-group"))
    assert injected == ["radio-group"]
    assert missing == []
    shim = files["src/components/ui/radio-group.tsx"]
    assert "export { RadioGroup, RadioGroupItem }" in shim
    assert "@radix-ui" not in shim  # dependency-free → no template rebuild


def test_missing_switch_gets_a_shim():
    files, injected, _ = ensure_ui_shims(_page("@/components/ui/switch"))
    assert injected == ["switch"]
    assert "export { Switch }" in files["src/components/ui/switch.tsx"]


def test_shipped_component_is_not_shimmed():
    files, injected, missing = ensure_ui_shims(_page("@/components/ui/button"))
    assert injected == []
    assert missing == []
    assert "src/components/ui/button.tsx" not in files


def test_existing_file_not_overwritten():
    files = _page("@/components/ui/radio-group")
    files["src/components/ui/radio-group.tsx"] = "// user version"
    out, injected, _ = ensure_ui_shims(files)
    assert injected == []
    assert out["src/components/ui/radio-group.tsx"] == "// user version"


def test_unknown_missing_component_is_reported_not_injected():
    files, injected, missing = ensure_ui_shims(_page("@/components/ui/carousel"))
    assert injected == []
    assert missing == ["carousel"]
    assert "src/components/ui/carousel.tsx" not in files


def test_non_ui_imports_ignored():
    _, injected, missing = ensure_ui_shims(_page("@/components/omnia/AppShell"))
    assert injected == [] and missing == []
