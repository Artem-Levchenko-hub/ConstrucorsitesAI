"""Image-resolver tests — focus on the fail-soft / dormant guarantees.

The Pexels success path needs network + a key, so it's exercised manually;
here we lock the invariants that must hold with no key (the prod default):
unresolved photo tags are STRIPPED (never left as a broken <img>), and a page
with no tags passes through byte-identical.
"""

import asyncio

from omnia_api.services.image_resolver import resolve_images


def test_photo_tag_stripped_when_source_off() -> None:
    # photo_source defaults to "off" → unresolved data-omnia-photo tags are
    # removed so the section's flat/mesh fallback shows, never a broken image.
    files = {
        "index.html": '<section><img data-omnia-photo="cafe interior" class="x"></section>'
    }
    out, resolved, total = asyncio.run(resolve_images(files, "proj-test"))
    assert "data-omnia-photo" not in out["index.html"]
    assert "<img" not in out["index.html"]
    assert total == 1
    assert resolved == 0


def test_no_tags_passthrough() -> None:
    files = {"index.html": "<section><h1>hi</h1></section>"}
    out, resolved, total = asyncio.run(resolve_images(files, "p"))
    assert out == files
    assert (resolved, total) == (0, 0)


def test_strip_unresolved_tags_removes_broken_imgs() -> None:
    from omnia_api.services.image_resolver import strip_unresolved_tags

    files = {
        "index.html": (
            '<section><div class="omnia-shader"></div>'
            '<img data-omnia-gen="coffee" alt="x" class="absolute inset-0">'
            "<h1>Hi</h1></section>"
        ),
        "style.css": "body{}",
    }
    out, n = strip_unresolved_tags(files)
    assert n == 1
    assert "data-omnia-gen" not in out["index.html"]
    assert "<img" not in out["index.html"]  # the broken tag is gone
    assert '<div class="omnia-shader"></div>' in out["index.html"]  # drawn bg kept
    assert "<h1>Hi</h1>" in out["index.html"]
    assert out["style.css"] == "body{}"  # non-html untouched


def test_strip_unresolved_tags_noop_when_resolved() -> None:
    from omnia_api.services.image_resolver import strip_unresolved_tags

    files = {"index.html": '<img src="https://cdn/x.png" alt="ok">'}
    out, n = strip_unresolved_tags(files)
    assert n == 0
    assert out == files
