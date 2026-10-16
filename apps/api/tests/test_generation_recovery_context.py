"""A handoff retains durable obligations, never arbitrary transcript dumps."""
import hashlib
import importlib
import importlib.util
from uuid import uuid4

import pytest

from tests.test_generation_recovery_policy import state
from yleum_api.services.generation.recovery_contract import RecoveryRequirement

ORIGINAL = "Make orders survive refresh and keep the original blue theme"


def context_state(original=ORIGINAL):
    digest = hashlib.sha256(original.encode()).hexdigest()
    base = state()
    return base.model_copy(update={
        "identity": base.identity.model_copy(update={"prompt_hash": digest}),
        "requirements": (RecoveryRequirement(id="original", text=original,
            origin_prompt_sha256=digest, check_kind="manual", required=False),)})


def context_builder():
    name = "yleum_api.services.generation.recovery_context"
    assert importlib.util.find_spec(name) is not None, "focused context builder missing"
    return importlib.import_module(name).build_recovery_context


def test_context_preserves_scope_obligations_intents_and_failed_approaches():
    build = context_builder()
    intent = uuid4()
    original = ORIGINAL
    base = context_state()
    current = base.model_copy(update={
        "requirements": (*base.requirements,
            RecoveryRequirement(id="persistence", text="orders survive refresh",
            origin_prompt_sha256=base.identity.prompt_hash,
            check_kind="db_persistence", required=True)),
        "unresolved_operation_ids": (intent,), "tried_strategies": ("retry migration",),
    })
    result = build(current, relevant_files={"src/orders.ts": "saveOrder(order)"},
                   diagnostic="TS2322 wrong type\n" * 1000)
    for text in (original, "persistence", str(intent), "retry migration", "saveOrder(order)"):
        assert text in result
    assert result.count("TS2322 wrong type") == 1
    assert len(result.encode()) < 24000


def test_context_redacts_every_surface_and_bounds_optional_source():
    build = context_builder()
    current = context_state().model_copy(update={"tried_strategies": ("API_KEY=hidden-strategy",)})
    result = build(current,
                   relevant_files={"API_KEY=hidden-path": "TOKEN=hidden-source\n" + "x" * 50000},
                   diagnostic="Authorization: Bearer hidden-diagnostic")
    assert "hidden-" not in result
    assert "[REDACTED]" in result
    assert len(result.encode()) <= 24000


def test_oversized_original_scope_is_rejected_instead_of_silently_dropped():
    build = context_builder()
    current = context_state("original scope " * 3000)
    with pytest.raises(ValueError, match=r"scope.*budget"):
        build(current, relevant_files={}, diagnostic="")


def test_source_credentials_are_removed_even_inside_code_or_private_keys():
    build = context_builder()
    result = build(context_state(), relevant_files={"src/config.ts":
        "const API_KEY = 'hidden-inline';\n"
        "-----BEGIN PRIVATE KEY-----\nhidden-pem\n-----END PRIVATE KEY-----"}, diagnostic="")
    assert "hidden-" not in result


@pytest.mark.parametrize("missing", [False, True])
def test_context_refuses_missing_or_mismatched_original_request(missing):
    build = context_builder()
    current = context_state()
    current = current.model_copy(update={"requirements": ()} if missing else {
        "identity": current.identity.model_copy(update={"prompt_hash": "f" * 64})})
    with pytest.raises(ValueError, match="original request"):
        build(current, relevant_files={"original_request": ORIGINAL}, diagnostic="")


@pytest.mark.parametrize("source", [
    'const config = {"apiKey": "hidden-json", "password": "hidden-password"}',
    'const apiKey = "hidden-js";',
    r'const apiKey = "escaped\"hidden-tail";',
    'const apiKey = "hidden-unclosed with hidden-spaces',
    'X-Api-Key: hidden-header',
    "-----BEGIN RSA PRIVATE KEY-----\nhidden-partial-pem",
    'const config = {"API_KEY": "' + "hidden-boundary" * 1000 + '"}',
])
def test_context_redacts_code_credentials_and_partial_private_keys_before_byte_cut(source):
    result = context_builder()(context_state(), relevant_files={"config.ts": source}, diagnostic="")
    assert "hidden-" not in result
    assert len(result.encode()) <= 24000


@pytest.mark.parametrize("source", [
    "const headers = { Authorization: 'Bearer hidden-inline-header' };",
    'const headers = { "X-Api-Key": "hidden-inline-api-header" };',
    "const password = `first hidden-template-value`;",
    "const token = `first line\nsecond hidden-multiline-value`;",
    "const token = `escaped \\` first\nsecond hidden-escaped-template`;",
    "const token = `first line\nsecond hidden-unclosed-template",
    "const token = `" + "x" * 8000 + " hidden-template-byte-tail`;",
])
def test_context_redacts_inline_headers_and_javascript_templates_without_tail_leaks(source):
    result = context_builder()(context_state(), relevant_files={"config.ts": source}, diagnostic="")
    assert "hidden-" not in result
    assert "[REDACTED]" in result
    assert len(result.encode()) <= 24000


@pytest.mark.parametrize("credential", [
    "apiKey=hidden-scalar", "API_KEY=hidden-env",
    "Authorization: Bearer hidden-bearer", "Authorization: Basic hidden-basic",
    "Proxy-Authorization: Bearer hidden-proxy", "Proxy-Authorization: Basic hidden-proxy-basic",
    "Proxy-Authorization: 'Token hidden quoted value'",
    "X-Authorization: Bearer hidden-custom-header", "proxyAuthorization: Basic hidden-camelcase",
    "password='hidden quoted value'", 'apiKey="hidden quoted value"',
    "token=`hidden first line\nsecond line`",
])
@pytest.mark.parametrize("prefix", ["", "Keep existing behavior. Use "])
def test_frozen_request_and_obligations_keep_scope_after_unquoted_credentials(credential, prefix):
    suffix = " and implement persistent orders with a blue theme."
    original = prefix + credential + suffix
    base = context_state(original)
    current = base.model_copy(update={"requirements": (*base.requirements,
        RecoveryRequirement(id="persistence", text="Preserve saved data. " + credential + suffix,
            origin_prompt_sha256=base.identity.prompt_hash,
            check_kind="db_persistence", required=True))})
    result = context_builder()(current, relevant_files={}, diagnostic="")
    assert "hidden-" not in result
    assert result.count(suffix) == 2
    assert "Preserve saved data." in result
    if prefix:
        assert prefix in result


@pytest.mark.parametrize("text", [
    'Use apiKey="hidden-unclosed and preserve orders.',
    "Use password='hidden-unclosed and preserve orders.",
    "Use token=`hidden-unclosed and preserve orders.",
    "Use token=`hidden-prefix${lookup()}` and preserve orders.",
    "-----BEGIN PRIVATE KEY-----\nhidden-unclosed\nand preserve orders.",
])
@pytest.mark.parametrize("obligation", [False, True])
def test_uncertain_credential_boundary_in_frozen_scope_fails_closed(text, obligation):
    current = context_state() if obligation else context_state(text)
    if obligation:
        current = current.model_copy(update={"requirements": (*current.requirements,
            RecoveryRequirement(id="persistence", text=text,
                origin_prompt_sha256=current.identity.prompt_hash,
                check_kind="db_persistence", required=True))})
    with pytest.raises(ValueError, match=r"credential boundary.*frozen scope"):
        context_builder()(current, relevant_files={}, diagnostic="")


@pytest.mark.parametrize("header", ["Authorization", "Proxy-Authorization",
                                    "X-Authorization", "proxyAuthorization"])
@pytest.mark.parametrize("scheme", ["Token", "JWT", "Digest", "Custom",
                                    "Bearer 'hidden quoted'", "Basic `hidden template`"])
@pytest.mark.parametrize("obligation", [False, True])
def test_unknown_authorization_in_frozen_scope_fails_closed(header, scheme, obligation):
    text = f"Use {header}: {scheme} hidden-credential and implement persistent orders."
    current = context_state() if obligation else context_state(text)
    if obligation:
        current = current.model_copy(update={"requirements": (*current.requirements,
            RecoveryRequirement(id="persistence", text=text,
                origin_prompt_sha256=current.identity.prompt_hash,
                check_kind="db_persistence", required=True))})
    with pytest.raises(ValueError, match=r"credential boundary.*frozen scope"):
        context_builder()(current, relevant_files={}, diagnostic="")


@pytest.mark.parametrize("header", ["Authorization", "Proxy-Authorization",
                                    "X-Authorization", "proxyAuthorization"])
@pytest.mark.parametrize("scheme", ["Token", "JWT", "Digest", "Custom",
                                    "Bearer 'hidden quoted'", "Basic `hidden template`"])
def test_optional_unknown_authorization_omits_uncertain_tail(header, scheme):
    source = f"const headers = {{ {header}: {scheme} hidden-credential hidden-tail }};"
    result = context_builder()(context_state(), relevant_files={"config.ts": source}, diagnostic="")
    assert "hidden-" not in result
    assert "[REDACTED]" in result
