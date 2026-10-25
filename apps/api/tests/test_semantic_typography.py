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
    assert len(fonts) == 8
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
