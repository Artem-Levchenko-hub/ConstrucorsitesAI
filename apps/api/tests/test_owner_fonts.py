import json

import pytest

from yleum_api.core.errors import ApiError
from yleum_api.services.font_catalog_sources import family_id
from yleum_api.services.owner_fonts_source import apply_owner_fonts, requested_font_names
from yleum_api.services.reference_images import request_identity


def test_name_and_trusted_link_are_identifiers_never_download_urls():
    assert family_id("Source Sans 3") == "source-sans-3"
    assert family_id("https://fonts.google.com/specimen/PT+Serif") == "pt-serif"
    assert family_id("https://fontsource.org/fonts/ibm-plex-mono") == "ibm-plex-mono"
    for bad in (
        "https://localhost/font.ttf",
        "file:///secret",
        "https://fontsource.org.evil/fonts/a",
        "https://fontsource.org:invalid/fonts/a",
    ):
        with pytest.raises(ApiError):
            family_id(bad)


def test_font_intent_avoids_content_mentions_and_handles_plain_name():
    assert requested_font_names("Добавь статью о шрифте Montserrat") == []
    assert requested_font_names("Используй шрифт Montserrat") == [("Montserrat", "all")]
    assert requested_font_names("Шрифт «PT Serif» для заголовков") == [("PT Serif", "heading")]
    assert requested_font_names("Не используй шрифт Roboto") == []
    assert requested_font_names("Use font Open Sans for headings") == [("Open Sans", "heading")]
    assert requested_font_names("Montserrat") == [("Montserrat", "all")]


def test_legacy_request_hashes_remain_byte_identical_and_font_role_is_bound():
    refs = [{"id": "a", "sha256": "f"}]
    assert request_identity("old", [], []) == "old"
    assert request_identity("old", refs, []) == request_identity("old", refs)
    font = {"id": "a", "sha256": "f", "role": "heading"}
    assert request_identity("old", [], [font]) != request_identity(
        "old", [], [{**font, "role": "body"}]
    )


def test_embedded_fonts_survive_text_snapshots_and_do_not_duplicate():
    binding = {"family": 'A";evil', "role": "heading", "sha256": "a" * 64, "id": "private-id"}
    bundle = {
        "faces": [
            {"data": "d09GMg==", "weight": 400, "style": "normal", "unicode_range": "U+0041-005A"}
        ],
        "license_text": "license",
    }
    original = {
        "src/app/globals.css": '@import "tailwindcss";\n',
        "typography.json": json.dumps({"version": 1, "heading": "Manrope", "body": "Onest"}),
    }
    out = apply_owner_fonts(original, [(binding, bundle)])
    assert json.loads(out["typography.json"])["body"] == "Onest"
    assert "data:font/woff2;base64,d09GMg==" in "".join(out.values())
    assert "private-id" not in "".join(out.values())
    assert 'A";evil' not in out["src/app/typography.css"]
    assert apply_owner_fonts(out, [(binding, bundle)]) == out


def test_new_version_of_same_family_cannot_select_old_face_by_import_order():
    binding = {"family": "Same", "role": "all", "sha256": "a" * 64}
    bundle = {
        "faces": [{"data": "AAAA", "weight": 400, "style": "normal", "unicode_range": "U+0041"}]
    }
    old = apply_owner_fonts({"src/app/globals.css": ""}, [(binding, bundle)])
    old_alias = json.loads(old["typography.json"])["owner_roles"]["body"]["alias"]
    new = apply_owner_fonts(
        old,
        [({**binding, "sha256": "b" * 64}, {"faces": [{**bundle["faces"][0], "data": "BBBB"}]})],
    )
    new_alias = json.loads(new["typography.json"])["owner_roles"]["body"]["alias"]
    assert old_alias != new_alias
    active = [
        css for path, css in new.items() if path.startswith("src/app/fonts/") and new_alias in css
    ]
    assert len(active) == 1 and "BBBB" in active[0]


def test_same_family_different_role_can_use_different_uploaded_versions():
    face = {"data": "AAAA", "weight": 400, "style": "normal", "unicode_range": "U+0041"}
    fonts = [
        ({"family": "Same", "role": "heading", "sha256": "a" * 64}, {"faces": [face]}),
        (
            {"family": "Same", "role": "body", "sha256": "b" * 64},
            {"faces": [{**face, "data": "BBBB"}]},
        ),
    ]
    out = apply_owner_fonts({"src/app/globals.css": ""}, fonts)
    roles = json.loads(out["typography.json"])["owner_roles"]
    assert roles["heading"]["alias"] != roles["body"]["alias"]


def test_reapplying_font_preserves_later_owner_design_notes():
    binding = {"family": "Same", "role": "heading", "sha256": "a" * 64}
    bundle = {
        "faces": [{"data": "AAAA", "weight": 400, "style": "normal", "unicode_range": "U+0041"}]
    }
    old = apply_owner_fonts(
        {"src/app/globals.css": "", "DESIGN.md": "Owner colours"}, [(binding, bundle)]
    )
    old["DESIGN.md"] += "\n## Business notes\nKeep owner notes.\n"
    updated = apply_owner_fonts(old, [(binding, bundle)])
    assert "Owner colours" in updated["DESIGN.md"]
    assert "Keep owner notes." in updated["DESIGN.md"]
