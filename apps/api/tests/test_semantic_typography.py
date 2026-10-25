import json

import pytest

from yleum_api.services.typography import choose_typography, seed_typography


def test_explicit_scandi_beats_fitness_and_cafe():
    for niche in ("фитнес", "кафе"):
        d = choose_typography("project", brief=f"{niche}, скандинавский минимализм, не брутализм")
        assert d.style == "scandinavian"
        assert d.heading == "Manrope"
        assert "Unbounded" not in (d.heading, d.body)


def test_explicit_font_beats_niche_and_negation():
    d = choose_typography("p", brief="Фитнес, шрифт Lora, не Unbounded")
    assert d.heading == "Lora"
    assert d.body == "Onest"


def test_explicit_pair_preserves_requested_order():
    d = choose_typography("p", brief="Heading Lora, body Manrope, fitness")
    assert (d.heading, d.body) == ("Lora", "Manrope")


def test_explicit_roles_override_mention_order():
    d = choose_typography("p", brief="Body Onest, heading Lora")
    assert (d.heading, d.body) == ("Lora", "Onest")


@pytest.mark.parametrize(
    "brief",
    [
        "Onest для текста и Lora для заголовков",
        "Lora для заголовков и Onest для текста",
        "Для текста Onest и для заголовков Lora",
        "Для заголовков Lora, для текста Onest",
        "Body Onest and heading Lora",
        "Heading Lora and body Onest",
        "Lora для заголовков, а Onest для текста",
    ],
)
def test_full_font_spans_and_role_clauses(brief):
    d = choose_typography("p", brief=brief)
    assert (d.heading, d.body) == ("Lora", "Onest")


@pytest.mark.parametrize(
    "brief",
    [
        'Шрифт "My Custom Display" для текста, Lora для заголовков',
        'Lora для заголовков и шрифт "My Custom Display" для текста',
        'Для текста шрифт "My Custom Display", для заголовков Lora',
    ],
)
def test_custom_name_is_not_a_role_label(brief):
    d = choose_typography("p", brief=brief)
    assert d.custom_request == "My Custom Display"
    assert (d.heading, d.body) == ("Lora", "system-ui")
    assert list(d.dependencies) == ["@fontsource/lora"]


def test_catalogue_name_text_is_not_body_role():
    d = choose_typography("p", brief="Golos Text для заголовков и Onest для текста")
    assert (d.heading, d.body) == ("Golos Text", "Onest")


def test_custom_family_full_span_does_not_install_its_catalogue_substring():
    d = choose_typography("p", brief='Шрифт "Onest Pro" для заголовков и Onest для текста')
    assert d.custom_request == "Onest Pro"
    assert (d.heading, d.body) == ("system-ui", "Onest")


def test_exclusions_survive_niche_and_preset_fallback():
    d = choose_typography("p", brief="Кофейня без Lora")
    assert "Lora" not in (d.heading, d.body)
    d = choose_typography("p", brief="Кафе без редакционного стиля", preset_id="editorial-luxury")
    assert d.style != "editorial"
    assert d.heading != "Lora"


def test_custom_heading_and_known_body_are_both_retained():
    d = choose_typography("p", brief='Шрифт "My Custom Display" для заголовков, Onest для текста')
    assert d.custom_request == "My Custom Display"
    assert d.heading == "system-ui" and d.body == "Onest"
    assert list(d.dependencies) == ["@fontsource/onest"]


def test_negation_contrast_and_expressive_opt_in():
    assert choose_typography("p", brief="Не брутализм а скандинавский фитнес").heading == "Manrope"
    assert choose_typography("p", brief="Фитнес в стиле брутализм").heading == "Unbounded"
    assert (
        choose_typography(
            "p",
            brief="Кафе без брутализма",
            preset_id="festival-brutalist",
        ).heading
        != "Unbounded"
    )
    assert choose_typography("p", brief="Кафе, шрифт без засечек").heading == "Manrope"


def test_contract_wire_shape_and_frozen_design_preserved():
    from dataclasses import asdict

    from yleum_api.services.design_plugin import build_design_contract, seed_design_memory

    contract = build_design_contract(
        project_id="p",
        project_name="Test",
        template="max_miniapp",
        brief="сканди кафе",
    )
    assert contract is not None
    assert set(asdict(contract)) == {
        "plugin_id",
        "version",
        "knowledge_source",
        "archetype",
        "preset_id",
        "prompt_block",
        "vision_context",
        "design_markdown",
    }
    assert "fonts.googleapis.com" not in contract.prompt_block
    assert "Manrope" in contract.prompt_block
    existing = {"DESIGN.md": "Owner typography", "package.json": "{}"}
    assert seed_design_memory(existing, contract) == existing


def test_catalogue_only_verified_selected_dependencies():
    from yleum_api.services.typography import font_catalogue

    fonts = font_catalogue()["fonts"]
    assert len(fonts) == 20
    for row in fonts.values():
        assert row["integrity"].startswith("sha512-")
        assert row["license"] == "OFL-1.1"
        assert 400 in row["weights"]
        assert all(len(asset["sha256"]) == 64 for asset in row["assets"].values())


def test_edit_context_uses_restored_choice_not_new_candidate():
    from yleum_api.services.typography import existing_typography_context

    seed = "Existing context\n\nOMNIA DESIGN PRO · new candidate Unbounded"
    files = {"typography.json": json.dumps({"version": 1, "heading": "Lora", "body": "Onest"})}
    context = existing_typography_context(seed, files)
    assert "Lora" in context and "Onest" in context
    assert "Unbounded" not in context
    assert "Existing context" in context
    assert existing_typography_context(seed, {}) == seed


def test_actual_edit_prompt_binds_accepted_typography(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from yleum_api.services.generation import agent_prompt
    from yleum_api.services.generation.contracts import StackPrompt

    async def snapshot():
        return {"typography.json": json.dumps({"version": 1, "heading": "Lora", "body": "Onest"})}

    monkeypatch.setattr(agent_prompt, "model_for_role", lambda *args, **kwargs: "synthetic")
    stack = StackPrompt(
        "Existing context\n\nOMNIA DESIGN PRO · candidate Unbounded",
        None,
        None,
        None,
        "system",
        False,
    )
    plan, _ = asyncio.run(
        agent_prompt.prepare_agent_prompt(
            stack=stack,
            factory=None,
            ids=None,
            project_info=SimpleNamespace(template="max_miniapp", restoration_context=None),
            prompt_text="Поменяй текст кнопки",
            runtime=SimpleNamespace(
                handle=SimpleNamespace(snapshot_files=snapshot, capabilities={}),
                coordinator=None,
            ),
            orchestrate=True,
            selected_elements=None,
            _is_edit=True,
            _is_continue=False,
            force_model=None,
        )
    )
    assert "Lora" in plan.user and "Onest" in plan.seed_context
    assert "Unbounded" not in plan.user


@pytest.mark.parametrize(
    "brief,expected",
    [
        ("Body Onest, heading Lora", "heading — Lora; body — Onest"),
        ("Кофейня без Lora", "heading — Onest; body — Onest"),
        (
            'Шрифт "My Custom Display" для заголовков, Onest для текста',
            "heading — system-ui; body — Onest",
        ),
    ],
)
def test_actual_build_prompt_keeps_semantic_decision(monkeypatch, brief, expected):
    import asyncio
    from types import SimpleNamespace

    from yleum_api.services.design_plugin import build_design_contract
    from yleum_api.services.generation import agent_prompt
    from yleum_api.services.generation.contracts import StackPrompt

    contract = build_design_contract(
        project_id="p",
        project_name="Test",
        template="max_miniapp",
        brief=brief,
    )
    assert contract is not None
    monkeypatch.setattr(agent_prompt, "model_for_role", lambda *args, **kwargs: "synthetic")
    monkeypatch.setattr(
        agent_prompt, "get_settings", lambda: SimpleNamespace(agent_builder_max_steps=30)
    )
    plan, _ = asyncio.run(
        agent_prompt.prepare_agent_prompt(
            stack=StackPrompt(contract.prompt_block, None, None, None, "system", False),
            factory=None,
            ids=None,
            project_info=SimpleNamespace(template="max_miniapp"),
            prompt_text=brief,
            runtime=SimpleNamespace(handle=None, coordinator=None),
            orchestrate=True,
            selected_elements=None,
            _is_edit=False,
            _is_continue=False,
            force_model=None,
        )
    )
    assert expected in plan.user
    assert "fonts.googleapis.com" not in plan.user
    if "My Custom Display" in brief:
        assert "он НЕ установлен" in plan.user


def test_negated_expressive_is_not_opt_in():
    d = choose_typography("p", brief="кафе без брутализма, не используйте Unbounded")
    assert "Unbounded" not in (d.heading, d.body)


def test_seed_is_local_pinned_and_consistent():
    from yleum_api.schemas.max_studio import MaxProjectConfigPayload
    from yleum_api.services.max_project_kit import render_max_starter_files

    d = choose_typography("p", brief="редакционный журнал")
    files = seed_typography(
        render_max_starter_files(
            MaxProjectConfigPayload(app_name="Test", app_type="custom", summary="Test")
        ),
        d,
    )
    manifest = json.loads(files["typography.json"])
    assert manifest["heading"] == d.heading
    assert "fonts.googleapis.com" not in files["src/app/typography.css"]
    assert "@fontsource/" in files["src/app/typography.css"]
    package = json.loads(files["package.json"])
    for name, version in d.dependencies.items():
        assert package["dependencies"][name] == version
        assert f"{name}@{version}" in files["pnpm-lock.yaml"]
    import hashlib

    for key, row in manifest["packages"].items():
        notice = files[f"public/fonts/licenses/{key}.txt"]
        assert hashlib.sha256(notice.encode()).hexdigest() == row["license_sha256"]
    assert "typography.css" in files["src/app/globals.css"]
    assert seed_typography(files, choose_typography("other", brief="брутализм")) == files


def test_unknown_custom_font_is_honest_and_not_fetched():
    d = choose_typography("p", brief='Используй шрифт "My Custom Display"')
    assert d.custom_request == "My Custom Display"
    assert d.heading == "system-ui"
    assert not d.dependencies


def test_explicit_preset_style_before_niche():
    d = choose_typography("p", brief="fitness tracker", preset_id="editorial-luxury")
    assert d.heading in ("Prata", "Lora")


def test_catalogue_covers_approved_pool_and_actual_verified_subsets():
    from yleum_api.services.typography import font_catalogue

    fonts = font_catalogue()["fonts"]
    assert {row["family"] for row in fonts.values()} >= {
        "Source Sans 3",
        "Source Serif 4",
        "Literata",
        "IBM Plex Sans",
        "IBM Plex Mono",
        "Geologica",
        "Commissioner",
        "Cormorant Garamond",
        "Vollkorn",
        "Nunito",
        "Oswald",
        "Caveat",
    }
    for row in fonts.values():
        assert len(row["assets"]) <= 16
        assert row["verification"]["russian_letters"] == 66
        assert row["verification"]["latin_letters_digits"] == 62
        # Prata 5.3.0 has neither a ruble glyph nor a latin-ext face upstream.
        assert row["verification"]["ruble"] is (row["family"] != "Prata")
        if row["family"] != "Prata":
            assert any("latin-ext" in path for path in row["imports"])


def test_approved_recipes_have_24_candidates_and_six_distinct_triples():
    from yleum_api.services.typography import font_catalogue

    catalogue = font_catalogue()
    recipes = catalogue["recipes"]
    assert len(recipes) == 24
    assert len({recipe["id"] for recipe in recipes}) == 24
    assert sum(len(set(recipe["roles"].values())) == 3 for recipe in recipes) == 6
    available = {font["family"] for font in catalogue["fonts"].values()}
    assert all(set(recipe["roles"].values()) <= available for recipe in recipes)


@pytest.mark.parametrize(
    "brief,expected",
    [
        ("Фитнес в мягком минималистичном стиле", "Commissioner"),
        ("Кофейня с индустриальными узкими плакатными заголовками", "Oswald"),
        ("Приложение с современной геометрией", "Geologica"),
        ("Спокойное длинное чтение", "Literata"),
        ("Ремесленный основательный характер", "Vollkorn"),
        ("Воздушная книжная элегантность", "Cormorant Garamond"),
    ],
)
def test_style_selects_distinct_new_families_before_niche(brief, expected):
    assert choose_typography("p", brief=brief).heading == expected


def test_explicit_three_roles_and_long_family_names_are_retained():
    decision = choose_typography(
        "p", brief="Lora для заголовков, Onest для текста и Caveat для акцентов"
    )
    assert (decision.heading, decision.body, decision.accent) == ("Lora", "Onest", "Caveat")
    assert len(decision.dependencies) == 3
    decision = choose_typography(
        "p", brief="Source Serif 4 для текста, IBM Plex Sans для заголовков, IBM Plex Mono для кода"
    )
    assert decision.body == "Source Serif 4" and decision.code == "IBM Plex Mono"
    assert len(decision.dependencies) == 3


def test_semantic_third_role_requires_a_real_function_and_respects_negation():
    with_code = choose_typography("p", brief="Технологичный интерфейс с блоками кода")
    assert with_code.code == "IBM Plex Mono" and len(with_code.families) == 3
    ordinary = choose_typography(
        "p", brief="Технологичный интерфейс без кода и рукописных акцентов"
    )
    assert ordinary.code is None and ordinary.accent is None
    assert len(ordinary.families) <= 2
    no_serifs = choose_typography("p", brief="Брутальная кофейня без засечек")
    assert no_serifs.heading == "Unbounded"


def test_owner_font_beats_soft_style_exclusion_and_body_only_keeps_display_safe():
    explicit = choose_typography("p", brief="Без брутализма, но шрифт Unbounded для заголовков")
    assert explicit.heading == "Unbounded"
    excluded = choose_typography("p", brief="Без Unbounded, брутальный интерфейс")
    assert excluded.heading != "Unbounded"


def test_triple_seed_exports_scoped_role_variables_and_ordinary_edits_freeze_all_roles():
    from yleum_api.schemas.max_studio import MaxProjectConfigPayload
    from yleum_api.services.max_project_kit import render_max_starter_files
    from yleum_api.services.typography import existing_typography_context

    decision = choose_typography("p", brief="Технологичный интерфейс с блоками кода")
    files = seed_typography(
        render_max_starter_files(
            MaxProjectConfigPayload(app_name="Test", app_type="custom", summary="Test")
        ),
        decision,
    )
    assert "--font-code: 'IBM Plex Mono'" in files["src/app/typography.css"]
    assert "code, pre" in files["src/app/typography.css"]
    assert json.loads(files["typography.json"])["code"] == "IBM Plex Mono"
    assert "IBM Plex Mono" in existing_typography_context("Seed", files)
    assert seed_typography(files, choose_typography("p", brief="уютная кофейня")) == files


def test_legacy_typography_decision_still_decodes_without_optional_roles():
    from pydantic import TypeAdapter

    from yleum_api.services.typography import TypographyDecision

    decision = TypographyDecision(
        **{
            "version": 1,
            "style": "scandinavian",
            "heading": "Manrope",
            "body": "Manrope",
            "families": ("manrope",),
            "custom_request": None,
        }
    )
    assert decision.accent is None and decision.ui is None and decision.code is None
    old_wire = json.dumps(
        {
            "version": 1,
            "style": "scandinavian",
            "heading": "Manrope",
            "body": "Manrope",
            "families": ["manrope"],
            "custom_request": None,
        }
    )
    assert TypeAdapter(TypographyDecision).validate_json(old_wire, strict=True) == decision


def test_sans_constraint_filters_inferred_serifs_but_explicit_owner_font_wins():
    inferred = choose_typography("p", brief="Редакционная кофейня без засечек")
    assert inferred.heading not in {"Lora", "Prata", "Source Serif 4"}
    explicit = choose_typography("p", brief="Шрифт Lora для заголовков, стиль без засечек")
    assert explicit.heading == "Lora"


@pytest.mark.parametrize(
    "brief",
    [
        "Scandinavian style. A cozy coffee shop",
        "Scandinavian style for a craft workshop",
        "Скандинавский стиль. Уютная ремесленная кофейня",
    ],
)
def test_explicit_scandinavian_style_beats_warm_or_craft_context(brief):
    decision = choose_typography("p", brief=brief)
    assert decision.heading == "Manrope" and decision.recipe_id == "R01"


@pytest.mark.parametrize("brief", ["A cozy coffee shop", "A craft workshop"])
def test_explicit_scandinavian_preset_beats_warm_or_craft_context(brief):
    decision = choose_typography("p", brief=brief, preset_id="scandinavian")
    assert decision.heading == "Manrope" and decision.recipe_id == "R01"


def test_seed_keeps_subset_ranges_so_latin_does_not_replace_cyrillic():
    from yleum_api.schemas.max_studio import MaxProjectConfigPayload
    from yleum_api.services.max_project_kit import render_max_starter_files
    from yleum_api.services.typography import font_catalogue

    decision = choose_typography("p", brief="шрифт Onest")
    files = seed_typography(
        render_max_starter_files(
            MaxProjectConfigPayload(app_name="Test", app_type="custom", summary="Test")
        ),
        decision,
    )
    css = files["src/app/typography.css"]
    for filename, asset in font_catalogue()["fonts"]["onest"]["assets"].items():
        assert f"url('~@fontsource/onest/files/{filename}')" in css
        assert f"unicode-range: {asset['unicode_range']}" in css
    assert "font-display: swap" in css
    assert '@import "@fontsource/onest/latin-' not in css
