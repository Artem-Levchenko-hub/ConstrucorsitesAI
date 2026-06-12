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


def test_openverse_photo_resolved(monkeypatch) -> None:
    # photo_source="openverse" routes data-omnia-photo through the Openverse
    # fetcher (dispatched by _fetch_stock_photo); the tag is rewritten to the
    # cached MinIO URL. Network + MinIO are stubbed — this locks the dispatch +
    # replacement wiring, complementing the off-path strip test above.
    import omnia_api.services.image_resolver as ir

    class _S:
        use_image_gen = False
        use_image_prompt_enrichment = False
        image_gen_max_unique = 8
        photo_source = "openverse"
        pexels_api_key = None

    monkeypatch.setattr(ir, "get_settings", lambda: _S())

    async def _fake_fetch(kw: str, pid: str) -> bytes:
        return b"\xff\xd8\xff\xe0jpeg"

    monkeypatch.setattr(ir, "_fetch_photo_openverse", _fake_fetch)
    monkeypatch.setattr(
        ir, "_upload_photo", lambda b, pid, kw: "http://minio/omnia-photos/p/x.jpg"
    )

    files = {"index.html": '<img data-omnia-photo="office team" class="x">'}
    out, resolved, total = asyncio.run(ir.resolve_images(files, "proj"))
    assert 'src="http://minio/omnia-photos/p/x.jpg"' in out["index.html"]
    assert "data-omnia-photo" not in out["index.html"]
    assert (resolved, total) == (1, 1)


def test_extract_jsx_template_gen_tag() -> None:
    # Catalog/list images rendered through a .map() carry a JSX *expression*
    # value: data-omnia-gen={`product shot, ${item.img}, luxury furniture`}.
    # The build-time resolver must see these too — a string-literal-only regex
    # leaves every catalog card with an empty src (the "blank product grid"
    # bug). The interpolation ${...} is dropped (unknown at build time); the
    # static descriptors become the prompt, group-collapsed to one real image.
    from omnia_api.services.image_resolver import extract_image_tags

    files = {
        "src/app/page.tsx": (
            "{items.map((item) => (\n"
            '  <img data-omnia-gen-group="catalog" '
            "data-omnia-gen={`product shot, ${item.img}, luxury furniture, "
            "soft studio lighting, 85mm`} "
            'alt={item.name} className="h-full w-full object-cover" />\n'
            "))}"
        ),
    }
    tags = extract_image_tags(files)
    assert len(tags) == 1, "JSX-expression data-omnia-gen must be extracted"
    t = tags[0]
    assert t.group == "catalog"
    # Interpolation removed, static descriptors kept, no dangling commas.
    assert "${" not in t.prompt
    assert ", ," not in t.prompt
    assert t.prompt.startswith("product shot")
    assert "luxury furniture" in t.prompt


def test_static_and_jsx_gen_tags_coexist() -> None:
    # A page mixing a static hero tag and a templated catalog tag must yield
    # BOTH — the string-literal hero and the JSX-expression catalog card.
    from omnia_api.services.image_resolver import extract_image_tags

    files = {
        "page.tsx": (
            '<img data-omnia-gen="hero interior, premium" alt="hero" />\n'
            "<img data-omnia-gen={`product shot, ${p.q}, studio`} alt={p.n} />"
        ),
    }
    prompts = sorted(t.prompt for t in extract_image_tags(files))
    assert len(prompts) == 2
    assert any(p.startswith("hero interior") for p in prompts)
    assert any(p.startswith("product shot") for p in prompts)
    assert all("${" not in p for p in prompts)


def test_fetch_stock_photo_off_returns_none(monkeypatch) -> None:
    # The dispatcher is the single seam that knows the provider — "off" yields
    # None so resolve_images strips the tag (no broken <img>).
    import omnia_api.services.image_resolver as ir

    class _S:
        photo_source = "off"

    monkeypatch.setattr(ir, "get_settings", lambda: _S())
    assert asyncio.run(ir._fetch_stock_photo("anything", "proj")) is None
