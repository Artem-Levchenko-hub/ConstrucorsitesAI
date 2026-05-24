from omnia_api.routers.messages import _ensure_kit_linked
from omnia_api.services.prompt_builder import KIT_FILES, build_system_prompt


def test_static_prompt_includes_style_and_animation_kit() -> None:
    sp = build_system_prompt("landing")
    assert "assets/omnia-kit.css" in sp
    assert "assets/omnia-kit.js" in sp
    assert "Aurora SaaS" in sp  # _STYLE_KIT preset
    assert "data-reveal-delay" in sp  # _ANIMATION_KIT class API


def test_fullstack_prompt_excludes_static_kit() -> None:
    fs = build_system_prompt("fullstack")
    assert "assets/omnia-kit.css" not in fs
    assert "Aurora SaaS" not in fs
    assert "Drizzle" in fs  # fullstack stack still present


def test_kit_files_constant() -> None:
    assert KIT_FILES == frozenset({"assets/omnia-kit.css", "assets/omnia-kit.js"})


def test_ensure_kit_linked_injects_when_missing() -> None:
    html = "<html><head><title>x</title></head><body></body></html>"
    out = _ensure_kit_linked({"index.html": html})["index.html"]
    assert "assets/omnia-kit.css" in out
    assert "assets/omnia-kit.js" in out
    assert out.index("omnia-kit.css") < out.index("</head>")  # injected before </head>


def test_ensure_kit_linked_idempotent_when_present() -> None:
    html = (
        '<html><head><link rel="stylesheet" href="assets/omnia-kit.css">'
        '<script src="assets/omnia-kit.js" defer></script></head><body></body></html>'
    )
    assert _ensure_kit_linked({"index.html": html})["index.html"] == html


def test_ensure_kit_linked_ignores_non_html() -> None:
    files = {"styles.css": "body{margin:0}"}
    assert _ensure_kit_linked(files) == files
