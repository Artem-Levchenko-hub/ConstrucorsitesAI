"""Pure unit tests for the style-override engine (no DB / app needed)."""

from omnia_api.services import overrides as ov

BASE = "<html><head><title>t</title></head><body><h1>Hi</h1><p>x</p></body></html>"


def test_element_color_rule_in_block_before_head_close():
    out = ov.apply_overrides(
        BASE, tokens=[], element_rules=[("h1", {"color": "#E11D48"})], font_links=[]
    )
    assert 'id="omnia-overrides"' in out
    assert "h1{ color: #E11D48 !important; }" in out
    assert out.index("omnia-overrides") < out.index("</head>")


def test_token_rule_emits_root_important():
    out = ov.apply_overrides(
        BASE, tokens=[("--accent", "#0EA5E9")], element_rules=[], font_links=[]
    )
    assert ":root{ --accent: #0EA5E9 !important; }" in out


def test_idempotent_no_duplicate_block():
    once = ov.apply_overrides(
        BASE, tokens=[], element_rules=[("h1", {"color": "#111111"})], font_links=[]
    )
    twice = ov.apply_overrides(
        once, tokens=[], element_rules=[("h1", {"color": "#111111"})], font_links=[]
    )
    assert once == twice
    assert once.count('id="omnia-overrides"') == 1


def test_font_link_dedup_and_rule():
    out = ov.apply_overrides(
        BASE,
        tokens=[],
        element_rules=[("p", {"font-family": "'Sora', system-ui, sans-serif"})],
        font_links=[
            ("Sora", "https://fonts.googleapis.com/css2?family=Sora&display=swap"),
            ("Sora", "https://dup"),
        ],
    )
    assert out.count('data-omnia-font="Sora"') == 1
    assert "font-family: 'Sora', system-ui, sans-serif !important;" in out


def test_carry_over_lifts_block_and_links():
    edited = ov.apply_overrides(
        BASE,
        tokens=[("--accent", "#123456")],
        element_rules=[("h1", {"color": "#abcdef"})],
        font_links=[("Sora", "https://fonts.googleapis.com/css2?family=Sora&display=swap")],
    )
    regen = "<html><head><title>new</title></head><body><h2>New</h2></body></html>"
    carried = ov.carry_over_overrides(edited, regen)
    assert 'id="omnia-overrides"' in carried
    assert "--accent: #123456" in carried
    assert 'data-omnia-font="Sora"' in carried


def test_carry_over_noop_when_no_overrides():
    plain = "<html><head></head><body></body></html>"
    assert ov.carry_over_overrides(plain, BASE) == BASE


def test_merge_accumulates_across_patches():
    step1 = ov.apply_overrides(
        BASE, tokens=[], element_rules=[("h1", {"color": "#111111"})], font_links=[]
    )
    step2 = ov.apply_overrides(
        step1,
        tokens=[("--accent", "#222222")],
        element_rules=[("h1", {"font-family": "'Sora', sans-serif"})],
        font_links=[("Sora", "https://fonts.googleapis.com/css2?family=Sora&display=swap")],
    )
    # earlier edit survived the second patch
    assert "color: #111111 !important;" in step2
    assert "font-family: 'Sora', sans-serif !important;" in step2
    assert "--accent: #222222 !important;" in step2
    # updating one prop keeps the other
    step3 = ov.apply_overrides(
        step2, tokens=[], element_rules=[("h1", {"color": "#999999"})], font_links=[]
    )
    assert "color: #999999 !important;" in step3
    assert "#111111" not in step3
    assert "font-family: 'Sora', sans-serif !important;" in step3


def test_value_sanitization_blocks_css_breakout():
    out = ov.apply_overrides(
        BASE,
        tokens=[],
        element_rules=[("h1", {"color": "#fff}</style><script>alert(1)</script>"})],
        font_links=[],
    )
    assert "<script>" not in out  # < / > / } stripped from the value
