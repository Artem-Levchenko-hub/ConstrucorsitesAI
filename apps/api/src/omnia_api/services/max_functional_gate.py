"""Signed browser acceptance gate for generated MAX Mini Apps.

The ordinary realtime gate proves a known API protocol. MAX products are
free-form mobile interfaces behind a managed, signed runtime, so their stable
contract is expressed through inert ``data-omnia-*`` hooks. This gate opens the
same signed preview as a real Studio reviewer, exercises main navigation and the
primary action, proves managed persistence across reload when requested, and
checks mobile/a11y/browser invariants. Missing evidence is red, never skipped.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from omnia_api.services.functional_gate import Check, FunctionalVerdict, summarize

_TIMEOUT_MS = 20_000
_MAX_NAV_CONTROLS = 8
_MAX_PLANNED_CAPABILITIES = 10
_MAX_PLANNED_ID_LENGTH = 120
_MAX_PLANNED_ACTION_LENGTH = 240
_PROOF_IDEMPOTENCY_HEADER = "X-Omnia-Proof-Key"
_PROOF_AUTHORIZATION_HEADER = "X-Omnia-Proof-Authorization"
_PROOF_INFRASTRUCTURE_HEADER = "x-omnia-proof-infrastructure"
_PROOF_OWNER_DEPENDENCY_HEADER = "x-omnia-proof-owner-dependency"


class _MaxProofInfrastructureUnavailable(RuntimeError):
    """Trusted managed proxy reported an unavailable proof dependency."""


class _MaxProofOwnerDependency(RuntimeError):
    """A required owner-controlled provider or test-mode is missing."""


@dataclass(frozen=True)
class MaxPlannedFlow:
    """Bounded deterministic contract supplied by the persisted MAX build plan."""

    screen_ids: tuple[str, ...]
    capability_ids: tuple[str, ...]
    capability_actions: tuple[tuple[str, str], ...]
    primary_action_id: str
    source_digest: str
    primary_action_kind: str = "managed_write"
    primary_integration_operation: str | None = None
    persistence_action_id: str | None = None


@dataclass(frozen=True)
class MaxCapabilityEvidence:
    """Objective evidence produced by activating one exact planned marker."""

    marker_count: int = 0
    clicked: bool = False
    observable_change: bool = False
    semantic_result: bool = False
    control_ready: bool = False
    action_label_match: bool = False
    accessible_name_match: bool = False
    primary_marker: bool = False
    persisted_marker: bool = False
    managed_write_statuses: tuple[int, ...] = ()
    causal_managed_write_statuses: tuple[int, ...] = ()
    causal_integration_statuses: tuple[int, ...] = ()
    causal_integration_operations: tuple[str, ...] = ()
    causal_integration_values: tuple[str, ...] = ()
    managed_write_ids: tuple[str, ...] = ()
    scoped_result_semantics: tuple[str, ...] = ()


@dataclass(frozen=True)
class MaxPlannedFlowEvidence:
    """JSON-like browser trace evaluated independently from Playwright."""

    visible_screen_states: tuple[tuple[str, ...], ...] = ()
    navigated_screen_ids: tuple[str, ...] = ()
    meaningful_screen_ids: tuple[str, ...] = ()
    capabilities: Mapping[str, MaxCapabilityEvidence] = field(default_factory=dict)
    primary_screen_transition: bool = False
    reload_read_statuses: tuple[int, ...] = ()
    reloaded_action_ids: tuple[str, ...] = ()
    persistence_ui_restored: bool = False


def _planned_flow_error(
    planned_flow: MaxPlannedFlow,
    *,
    require_persistence: bool,
) -> str | None:
    groups = (
        ("screen_ids", planned_flow.screen_ids, 2, _MAX_NAV_CONTROLS),
        ("capability_ids", planned_flow.capability_ids, 1, _MAX_PLANNED_CAPABILITIES),
    )
    for name, values, minimum, maximum in groups:
        if isinstance(values, str) or not isinstance(values, tuple):
            return f"{name} must be a tuple"
        if not minimum <= len(values) <= maximum:
            return f"{name} must contain {minimum}..{maximum} ids"
        if any(
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > _MAX_PLANNED_ID_LENGTH
            for value in values
        ):
            return f"{name} contains an invalid id"
        if len(set(values)) != len(values):
            return f"{name} contains duplicate ids"
    actions = planned_flow.capability_actions
    if not isinstance(actions, tuple) or len(actions) != len(planned_flow.capability_ids):
        return "capability_actions must describe every planned capability"
    action_ids: list[str] = []
    for item in actions:
        if (
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[0], str)
            or not isinstance(item[1], str)
            or not item[1]
            or item[1] != item[1].strip()
            or len(item[1]) > _MAX_PLANNED_ACTION_LENGTH
        ):
            return "capability_actions contains an invalid action contract"
        action_ids.append(item[0])
    if tuple(action_ids) != planned_flow.capability_ids:
        return "capability_actions must match capability_ids in order"
    if (
        not isinstance(planned_flow.source_digest, str)
        or len(planned_flow.source_digest) != 64
        or any(char not in "0123456789abcdef" for char in planned_flow.source_digest)
    ):
        return "source_digest must be a lowercase SHA-256 digest"
    if planned_flow.primary_action_id not in planned_flow.capability_ids:
        return "primary_action_id is not a planned capability"
    if planned_flow.primary_action_kind not in {
        "local_navigation",
        "managed_write",
        "catalog_read",
    }:
        return "primary_action_kind is invalid"
    if require_persistence != (planned_flow.primary_action_kind == "managed_write"):
        return "primary_action_kind and persistence requirement disagree"
    if planned_flow.primary_action_kind == "catalog_read":
        if planned_flow.primary_integration_operation != "catalog":
            return "catalog_read requires the catalog integration operation"
    elif planned_flow.primary_integration_operation is not None:
        return "only catalog_read may declare an integration operation"
    if require_persistence:
        if not planned_flow.persistence_action_id:
            return "persistence_action_id is required"
        if planned_flow.persistence_action_id not in planned_flow.capability_ids:
            return "persistence_action_id is not a planned capability"
    elif (
        planned_flow.persistence_action_id is not None
        and planned_flow.persistence_action_id not in planned_flow.capability_ids
    ):
        return "persistence_action_id is not a planned capability"
    return None


def _normalise_action_semantics(value: object) -> str:
    return " ".join(str(value).casefold().split())


def _contains_action_semantics(value: object, expected_action: str) -> bool:
    expected = _normalise_action_semantics(expected_action)
    return bool(expected) and expected in _normalise_action_semantics(value)


def _capability_control_ready(action: MaxCapabilityEvidence | None) -> bool:
    """Structural evidence only: present, reachable, semantic, enabled and labelled."""

    return bool(
        action
        and action.marker_count == 1
        and action.control_ready
        and action.action_label_match
        and action.accessible_name_match
    )


def _primary_has_meaningful_result(
    action: MaxCapabilityEvidence | None,
    expected_action: str,
    *,
    screen_transition: bool,
    require_persistence: bool,
    primary_action_kind: str,
    expected_integration_operation: str | None,
) -> bool:
    """Prove the primary flow by transition or canonical managed response, never DOM churn."""

    if not action or not action.clicked or not action.observable_change:
        return False
    successful_write = any(
        200 <= status < 300 for status in action.causal_managed_write_statuses
    )
    unexpected_write = (
        any(200 <= status < 300 for status in action.managed_write_statuses)
        or successful_write
        or bool(action.managed_write_ids)
    )
    if primary_action_kind == "local_navigation":
        return screen_transition and not require_persistence and not unexpected_write
    if not action.semantic_result:
        return False
    canonical_result = bool(action.managed_write_ids) and any(
        _contains_action_semantics(result, expected_action)
        and any(record_id in result for record_id in action.managed_write_ids)
        for result in action.scoped_result_semantics
    )
    successful_integration = bool(
        expected_integration_operation
        and expected_integration_operation in action.causal_integration_operations
        and any(200 <= status < 300 for status in action.causal_integration_statuses)
    )
    integration_result = any(
        _contains_action_semantics(result, expected_action)
        and any(
            _normalise_action_semantics(value) in _normalise_action_semantics(result)
            for value in action.causal_integration_values
        )
        for result in action.scoped_result_semantics
    )
    if primary_action_kind == "managed_write":
        return require_persistence and successful_write and canonical_result
    if primary_action_kind == "catalog_read":
        return (
            not require_persistence
            and not unexpected_write
            and successful_integration
            and integration_result
        )
    return False


def _proof_idempotency_key(planned_flow: MaxPlannedFlow, capability_id: str) -> str:
    """Stable per-plan/per-capability key: replayed browser proof cannot duplicate it."""

    material = json.dumps(
        {
            "capability": capability_id,
            "capability_actions": planned_flow.capability_actions,
            "capabilities": planned_flow.capability_ids,
            "persistence": planned_flow.persistence_action_id,
            "primary": planned_flow.primary_action_id,
            "primary_action_kind": planned_flow.primary_action_kind,
            "primary_integration_operation": planned_flow.primary_integration_operation,
            "screens": planned_flow.screen_ids,
            "source_digest": planned_flow.source_digest,
            "version": 6,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _proof_headers(
    planned_flow: MaxPlannedFlow,
    project_id: UUID | str,
    capability_id: str,
) -> dict[str, str]:
    """Bind sandbox access to the API-only secret and exact source revision."""

    from omnia_api.services.max_proof_authorization import issue_max_proof_authorization

    proof_key = _proof_idempotency_key(planned_flow, capability_id)
    return {
        _PROOF_IDEMPOTENCY_HEADER: proof_key,
        _PROOF_AUTHORIZATION_HEADER: issue_max_proof_authorization(
            project_id,
            proof_key=proof_key,
            source_digest=planned_flow.source_digest,
            capability_id=capability_id,
        ),
    }


def _response_is_proof_infrastructure(response: object) -> bool:
    headers = getattr(response, "headers", {})
    if callable(headers):
        try:
            headers = headers()
        except Exception:
            return False
    if not isinstance(headers, Mapping):
        return False
    return str(headers.get(_PROOF_INFRASTRUCTURE_HEADER) or "").casefold() == "unavailable"


def _response_is_owner_dependency(response: object) -> bool:
    headers = getattr(response, "headers", {})
    if callable(headers):
        try:
            headers = headers()
        except Exception:
            return False
    if not isinstance(headers, Mapping):
        return False
    return str(headers.get(_PROOF_OWNER_DEPENDENCY_HEADER) or "").casefold() == "required"


def evaluate_planned_flow_evidence(
    planned_flow: MaxPlannedFlow,
    evidence: MaxPlannedFlowEvidence,
    *,
    require_persistence: bool,
) -> list[Check]:
    """Map a bounded browser trace to exact plan-coverage checks."""

    error = _planned_flow_error(planned_flow, require_persistence=require_persistence)
    if error:
        return [Check("max_planned_flow_contract", False, error)]

    planned_screens = set(planned_flow.screen_ids)
    reached: set[str] = set()
    ambiguous_states = 0
    for state in evidence.visible_screen_states:
        active = planned_screens.intersection(state)
        if len(active) == 1:
            reached.update(active)
        elif len(active) > 1:
            ambiguous_states += 1
    navigated = set(evidence.navigated_screen_ids)
    meaningful = set(evidence.meaningful_screen_ids)
    missing_screens = [
        screen
        for screen in planned_flow.screen_ids
        if screen not in reached or screen not in navigated or screen not in meaningful
    ]
    reached_screens = [
        screen
        for screen in planned_flow.screen_ids
        if screen in reached and screen in navigated and screen in meaningful
    ]

    capability_actions = dict(planned_flow.capability_actions)
    failed_capabilities: list[str] = []
    for capability_id in planned_flow.capability_ids:
        action = evidence.capabilities.get(capability_id)
        if not _capability_control_ready(action):
            failed_capabilities.append(capability_id)

    primary = evidence.capabilities.get(planned_flow.primary_action_id)
    primary_ok = bool(
        _capability_control_ready(primary)
        and primary
        and _primary_has_meaningful_result(
            primary,
            capability_actions[planned_flow.primary_action_id],
            screen_transition=evidence.primary_screen_transition,
            require_persistence=require_persistence,
            primary_action_kind=planned_flow.primary_action_kind,
            expected_integration_operation=planned_flow.primary_integration_operation,
        )
        and primary.primary_marker
    )
    checks = [
        Check(
            "max_planned_flow_contract",
            True,
            (
                f"screens={len(planned_flow.screen_ids)}, "
                f"capabilities={len(planned_flow.capability_ids)}"
            ),
        ),
        Check(
            "max_planned_screens",
            not missing_screens and ambiguous_states == 0,
            (
                f"reached={','.join(reached_screens) or 'none'}; "
                f"missing={','.join(missing_screens) or 'none'}; "
                f"navigated={','.join(sorted(navigated.intersection(planned_screens))) or 'none'}; "
                "meaningful="
                f"{','.join(sorted(meaningful.intersection(planned_screens))) or 'none'}; "
                f"ambiguous_states={ambiguous_states}"
            ),
        ),
        Check(
            "max_planned_capabilities",
            not failed_capabilities,
            (
                "all planned capability controls are unique, reachable, enabled and labelled"
                if not failed_capabilities
                else "missing, duplicate, disabled or unlabelled=" + ",".join(failed_capabilities)
            ),
        ),
        Check(
            "max_primary_action_interaction",
            primary_ok,
            (
                f"planned action {planned_flow.primary_action_id} produced an independent result"
                if primary_ok
                else (
                    f"planned action {planned_flow.primary_action_id} lacked a planned screen "
                    "transition or canonical managed response"
                )
            ),
        ),
    ]
    if require_persistence:
        persistence_id = planned_flow.persistence_action_id or ""
        persistence = evidence.capabilities.get(persistence_id)
        write_ok = bool(
            persistence
            and persistence.clicked
            and persistence.persisted_marker
            and any(200 <= status < 300 for status in persistence.causal_managed_write_statuses)
        )
        restored_ids = set(evidence.reloaded_action_ids)
        restored = bool(persistence and restored_ids.intersection(persistence.managed_write_ids))
        read_ok = any(200 <= status < 300 for status in evidence.reload_read_statuses)
        write_statuses = list(persistence.causal_managed_write_statuses) if persistence else "none"
        checks.append(
            Check(
                "max_reload_persistence",
                write_ok and read_ok and restored and evidence.persistence_ui_restored,
                (
                    f"action={persistence_id}; "
                    f"writes={write_statuses}; "
                    f"reload_reads={list(evidence.reload_read_statuses) or 'none'}; "
                    f"restored_written_id={restored}; "
                    f"restored_ui_state={evidence.persistence_ui_restored}"
                ),
            )
        )
    return checks


def evaluate_static_observation(observation: Mapping[str, Any]) -> list[Check]:
    """Convert browser facts into deterministic mobile/a11y checks."""

    try:
        nav_count = int(observation.get("nav_count", 0))
        heading_count = int(observation.get("heading_count", 0))
        unlabeled = int(observation.get("unlabeled_controls", 0))
        fake_controls = int(observation.get("fake_controls", 0))
        small_targets = int(observation.get("small_targets", 0))
        overflow = int(observation.get("horizontal_overflow", 0))
        primary_count = int(observation.get("primary_count", 0))
    except (TypeError, ValueError):
        return [Check("max_dom_contract", False, "browser returned malformed DOM facts")]
    return [
        Check(
            "max_main_navigation",
            nav_count >= 2,
            f"{nav_count} semantic data-omnia-screen-nav control(s)",
        ),
        Check(
            "max_primary_action",
            primary_count >= 1,
            f"{primary_count} visible data-omnia-primary-action control(s)",
        ),
        Check(
            "max_mobile_layout",
            overflow == 0,
            "no horizontal overflow" if overflow == 0 else f"overflow by {overflow}px",
        ),
        Check(
            "max_accessibility",
            heading_count >= 1 and unlabeled == 0 and fake_controls == 0 and small_targets == 0,
            (
                f"headings={heading_count}, unlabeled={unlabeled}, "
                f"non-semantic={fake_controls}, undersized_targets={small_targets}"
            ),
        ),
    ]


async def _visible_marker_values(
    page: Any,
    attribute: str,
    *,
    viewport_only: bool,
) -> tuple[str, ...]:
    values = await page.evaluate(
        """([attribute, viewportOnly]) => {
          const visible = (el) => {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            if (!(r.width > 0 && r.height > 0) || s.display === 'none' ||
                s.visibility === 'hidden' || Number(s.opacity || 1) <= 0) return false;
            return !viewportOnly || (
              r.right > 0 && r.bottom > 0 && r.left < innerWidth && r.top < innerHeight
            );
          };
          return [...document.querySelectorAll(`[${attribute}]`)]
            .filter(visible)
            .map((el) => (el.getAttribute(attribute) || '').trim())
            .filter(Boolean);
        }""",
        [attribute, viewport_only],
    )
    return tuple(dict.fromkeys(str(value) for value in values if str(value)))


async def _meaningful_visible_screen_values(page: Any) -> tuple[str, ...]:
    """Return real active screen roots; reject offscreen, 1px and empty hooks."""

    values = await page.evaluate(
        """() => {
          const visible = (el) => {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width >= 48 && r.height >= 48 && s.display !== 'none' &&
              s.visibility !== 'hidden' && Number(s.opacity || 1) > 0 &&
              r.right > 0 && r.bottom > 0 && r.left < innerWidth && r.top < innerHeight;
          };
          return [...document.querySelectorAll('[data-omnia-screen]')]
            .filter(visible)
            .filter((el) => {
              const text = (el.innerText || '').replace(/\\s+/g, ' ').trim();
              if (text.length >= 3) return true;
              return [...el.querySelectorAll(
                'button,a,input,select,textarea,img,canvas,[role="heading"],[role="img"]'
              )].some(visible);
            })
            .map((el) => (el.getAttribute('data-omnia-screen') || '').trim())
            .filter(Boolean);
        }"""
    )
    return tuple(dict.fromkeys(str(value) for value in values if str(value)))


async def _visible_capability_control(page: Any, capability_id: str) -> tuple[Any | None, int]:
    active_screens = set(
        await _visible_marker_values(page, "data-omnia-screen", viewport_only=True)
    )
    markers = page.locator("[data-omnia-capability]")
    marker_count = 0
    selected = None
    for index in range(await markers.count()):
        marker = markers.nth(index)
        if await marker.get_attribute("data-omnia-capability") != capability_id:
            continue
        marker_count += 1
        if selected is not None or not await marker.is_visible():
            continue
        owner = str(
            await marker.evaluate(
                "(el) => el.closest('[data-omnia-screen]')?.getAttribute('data-omnia-screen') || ''"
            )
        )
        if owner:
            if owner in active_screens:
                selected = marker
            continue
        in_viewport = bool(
            await marker.evaluate(
                """(el) => {
                  const r = el.getBoundingClientRect();
                  return r.right > 0 && r.bottom > 0 && r.left < innerWidth && r.top < innerHeight;
                }"""
            )
        )
        if in_viewport:
            selected = marker
    return selected, marker_count


async def _active_capability_values(
    page: Any,
    active_screen_ids: tuple[str, ...],
) -> tuple[str, ...]:
    values = await page.evaluate(
        """(activeScreenIds) => {
          const active = new Set(activeScreenIds);
          const visible = (el) => {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' &&
              s.visibility !== 'hidden' && Number(s.opacity || 1) > 0;
          };
          return [...document.querySelectorAll('[data-omnia-capability]')]
            .filter(visible)
            .filter((el) => {
              const owner = el.closest('[data-omnia-screen]');
              if (owner) return active.has(owner.getAttribute('data-omnia-screen') || '');
              const r = el.getBoundingClientRect();
              return r.right > 0 && r.bottom > 0 && r.left < innerWidth && r.top < innerHeight;
            })
            .map((el) => (el.getAttribute('data-omnia-capability') || '').trim())
            .filter(Boolean);
        }""",
        list(active_screen_ids),
    )
    return tuple(dict.fromkeys(str(value) for value in values if str(value)))


async def _scoped_action_result_fingerprint(page: Any, capability_id: str) -> str:
    """Fingerprint only visible semantic results explicitly owned by this action."""

    return str(
        await page.evaluate(
            """(capabilityId) => {
              const visible = (el) => {
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.display !== 'none' &&
                  s.visibility !== 'hidden' && Number(s.opacity || 1) > 0;
              };
              const results = [...document.querySelectorAll('[data-omnia-action-result]')]
                .filter(visible)
                .filter((el) =>
                  (el.getAttribute('data-omnia-action-result') || '').trim() === capabilityId
                );
              return JSON.stringify(results.map((el) => ({
                text: (el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 4000),
                actionLabel: el.getAttribute('data-omnia-action-result-label') || '',
                recordId: el.getAttribute('data-omnia-record-id') || '',
                state: el.getAttribute('data-omnia-state') || '',
                value: 'value' in el ? String(el.value || '').slice(0, 1000) : '',
                checked: 'checked' in el ? Boolean(el.checked) : false,
                pressed: el.getAttribute('aria-pressed') || '',
                selected: el.getAttribute('aria-selected') || '',
              })));
            }""",
            capability_id,
        )
    )


async def _scoped_action_feedback(page: Any, capability_id: str) -> tuple[str, int]:
    """Return visible scoped error evidence and remaining success-marker count."""

    value = await page.evaluate(
        """(capabilityId) => {
          const visible = (el) => {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' &&
              s.visibility !== 'hidden' && Number(s.opacity || 1) > 0;
          };
          const errors = [...document.querySelectorAll('[data-omnia-action-error]')]
            .filter(visible)
            .filter((el) =>
              (el.getAttribute('data-omnia-action-error') || '').trim() === capabilityId
            )
            .filter((el) =>
              el.getAttribute('role') === 'alert' || Boolean(el.getAttribute('aria-live'))
            )
            .map((el) => ({
              text: (el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 2000),
              role: el.getAttribute('role') || '',
            }))
            .filter((item) => item.text.length > 0);
          const successes = [...document.querySelectorAll('[data-omnia-action-result]')]
            .filter(visible)
            .filter((el) =>
              (el.getAttribute('data-omnia-action-result') || '').trim() === capabilityId
            ).length;
          return { errors, successes };
        }""",
        capability_id,
    )
    if not isinstance(value, Mapping):
        return "[]", 0
    errors = value.get("errors")
    successes = value.get("successes")
    try:
        success_count = int(successes or 0)
    except (TypeError, ValueError):
        success_count = 0
    return json.dumps(errors if isinstance(errors, list) else [], sort_keys=True), success_count


async def _prepare_primary_form_controls(action: Any, seed: str) -> int:
    """Act like a user: fill real visible controls around the primary CTA."""

    value = await action.evaluate(
        """(cta, seed) => {
          const visible = (el) => {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' &&
              s.visibility !== 'hidden' && Number(s.opacity || 1) > 0;
          };
          const scope = cta.closest('form,[data-omnia-primary-flow]');
          if (!scope) return 0;
          const controls = [...scope.querySelectorAll('input,textarea,select')]
            .filter(visible)
            .filter((el) => !el.disabled && !el.readOnly)
            .slice(0, 12);
          let changed = 0;
          const dispatch = (el) => {
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
          };
          for (const el of controls) {
            if (el instanceof HTMLSelectElement) {
              const option = [...el.options].find((item) => !item.disabled && item.value);
              if (!option) continue;
              el.value = option.value;
              dispatch(el);
              changed += 1;
              continue;
            }
            if (el instanceof HTMLInputElement && ['checkbox', 'radio'].includes(el.type)) {
              if (!el.checked) el.click();
              changed += 1;
              continue;
            }
            if (!(el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement)) continue;
            const type = el instanceof HTMLInputElement ? el.type : 'textarea';
            if (['hidden', 'submit', 'button', 'reset', 'file', 'image'].includes(type)) continue;
            if (String(el.value || '').trim() && el.checkValidity()) continue;
            const fixtures = {
              date: '2030-01-15',
              'datetime-local': '2030-01-15T10:30',
              email: `proof-${seed}@example.invalid`,
              month: '2030-01',
              number: '1',
              password: `Proof-${seed}-A1!`,
              search: `Omnia ${seed}`,
              tel: '+79990000000',
              time: '10:30',
              url: 'https://example.invalid/proof',
              week: '2030-W03',
            };
            let next = fixtures[type] || `Omnia ${seed}`;
            if (type === 'number' && el instanceof HTMLInputElement) {
              const minimum = Number(el.min);
              const maximum = Number(el.max);
              const candidate = Number.isFinite(minimum) ? minimum : 1;
              next = String(Number.isFinite(maximum) ? Math.min(candidate, maximum) : candidate);
            } else if (el instanceof HTMLInputElement && el.min) {
              next = el.min;
            }
            const minimumLength = Number(el.getAttribute('minlength') || 0);
            if (minimumLength > next.length) next = next.padEnd(minimumLength, 'x');
            const prototype = el instanceof HTMLInputElement
              ? HTMLInputElement.prototype
              : HTMLTextAreaElement.prototype;
            const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
            if (!setter) continue;
            setter.call(el, next);
            dispatch(el);
            changed += 1;
          }
          return changed;
        }""",
        seed,
    )
    return int(value) if isinstance(value, int) else 0


def _scoped_result_semantics(fingerprint: str) -> tuple[str, ...]:
    """Extract only explicit, scoped user-visible action outcome semantics."""

    try:
        results = json.loads(fingerprint)
    except (TypeError, ValueError, json.JSONDecodeError):
        return ()
    if not isinstance(results, list):
        return ()
    values: list[str] = []
    for result in results:
        if not isinstance(result, Mapping):
            continue
        row: list[str] = []
        for key in ("actionLabel", "recordId", "text"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                row.append(value.strip())
        if row:
            values.append(" ".join(row))
    return tuple(dict.fromkeys(values))


async def _visible_record_ids(page: Any) -> tuple[str, ...]:
    """Return server record ids currently rendered in visible product UI."""

    value = await page.evaluate(
        """() => {
          const visible = (el) => {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.display !== 'none' &&
              s.visibility !== 'hidden' && Number(s.opacity || 1) > 0;
          };
          return [...document.querySelectorAll('[data-omnia-record-id]')]
            .filter(visible)
            .map((el) => (el.getAttribute('data-omnia-record-id') || '').trim())
            .filter(Boolean)
            .slice(0, 200);
        }"""
    )
    if not isinstance(value, list):
        return ()
    return tuple(dict.fromkeys(str(item) for item in value if str(item)))


async def _response_json(response: Any) -> Mapping[str, Any] | None:
    try:
        value = await response.json()
    except Exception:
        return None
    return value if isinstance(value, Mapping) else None


def _integration_operation(response: Any) -> str:
    request = getattr(response, "request", None)
    path = urlsplit(str(getattr(request, "url", ""))).path
    marker = "/api/omnia/integrations/"
    return path.split(marker, 1)[1].strip("/") if marker in path else ""


def _catalog_item_values(payload: Any) -> tuple[str, ...]:
    """Extract only real catalog item identity, never provider/currency metadata."""

    items = payload.get("items") if isinstance(payload, Mapping) else None
    if not isinstance(items, list) or not items:
        return ()
    values: list[str] = []
    for item in items[:24]:
        if not isinstance(item, Mapping):
            continue
        for item_field in ("name", "id"):
            value = item.get(item_field)
            if isinstance(value, str):
                clean = " ".join(value.split())
                if 3 <= len(clean) <= 120:
                    values.append(clean)
        if len(values) >= 40:
            break
    return tuple(dict.fromkeys(values[:40]))


async def _integration_response_evidence(
    responses: list[Any],
    *,
    expected_operation: str | None,
) -> tuple[tuple[int, ...], tuple[str, ...], tuple[str, ...]]:
    """Accept only real catalog data; generic integration status is never a business result."""

    statuses: list[int] = []
    operations: list[str] = []
    values: list[str] = []
    for response in responses:
        status = int(getattr(response, "status", 0))
        operation = _integration_operation(response)
        if operation != expected_operation or operation != "catalog" or not 200 <= status < 300:
            continue
        try:
            payload = await response.json()
        except Exception:
            continue
        payload_values = _catalog_item_values(payload)
        if not payload_values:
            continue
        statuses.append(status)
        operations.append(operation)
        values.extend(payload_values)
    return (
        tuple(statuses),
        tuple(dict.fromkeys(operations)),
        tuple(dict.fromkeys(values)),
    )


async def _created_action_ids(responses: list[Any]) -> tuple[str, ...]:
    ids: list[str] = []
    for response in responses:
        if not 200 <= int(getattr(response, "status", 0)) < 300:
            continue
        payload = await _response_json(response)
        action = payload.get("action") if payload else None
        action_id = str(action.get("id") or "") if isinstance(action, Mapping) else ""
        if action_id:
            ids.append(action_id)
    return tuple(dict.fromkeys(ids))


def _request_action_contract(request: Any) -> tuple[str | None, Mapping[str, Any] | None]:
    """Read exact managed action type and payload from one POST body."""

    try:
        post_data = getattr(request, "post_data", None)
        if callable(post_data):
            post_data = post_data()
        payload = json.loads(str(post_data or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, None
    if not isinstance(payload, Mapping):
        return None, None
    action_type = payload.get("actionType")
    action_type = action_type if isinstance(action_type, str) and action_type else None
    action_payload = payload.get("payload")
    return action_type, action_payload if isinstance(action_payload, Mapping) else None


def _request_action_type(request: Any) -> str | None:
    """Read exact managed action type without trusting a coincidental POST."""

    return _request_action_contract(request)[0]


def _request_proof_signature(request: Any) -> tuple[str, str, str]:
    """Bind proof evidence to one exact managed request without logging its body."""

    url = str(getattr(request, "url", ""))
    method = str(getattr(request, "method", "")).upper()
    post_data = getattr(request, "post_data", None)
    if callable(post_data):
        post_data = post_data()
    raw_body = str(post_data or "")
    try:
        parsed = json.loads(raw_body) if raw_body else None
        canonical_body = json.dumps(
            parsed,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        canonical_body = raw_body
    body_digest = hashlib.sha256(canonical_body.encode("utf-8")).hexdigest()
    return url, method, body_digest


def _primary_failure_route_handler(
    capability_id: str,
    *,
    probe_active: list[bool],
    intercepted: list[bool],
    captured: set[tuple[str, str, str]],
    expected_requests: set[tuple[str, str, str]],
) -> Callable[[Any], Awaitable[None]]:
    """Build a loop-safe Playwright handler for one primary failure probe."""

    async def handler(route: Any) -> None:
        request = route.request
        url = str(getattr(request, "url", ""))
        method = str(getattr(request, "method", "")).upper()
        action_type, _ = _request_action_contract(request)
        is_action = (
            "/api/omnia/actions" in url
            and method == "POST"
            and action_type == capability_id
        )
        is_integration = "/api/omnia/integrations/" in url
        if probe_active[0] and (is_action or is_integration):
            intercepted[0] = True
            expected = _request_proof_signature(request)
            captured.add(expected)
            expected_requests.add(expected)
            await route.fulfill(
                status=503,
                content_type="application/json",
                body='{"error":{"message":"proof failure"}}',
            )
            return
        await route.continue_()

    return handler


async def _listed_action_ids(responses: list[Any]) -> tuple[str, ...]:
    ids: list[str] = []
    for response in responses:
        if not 200 <= int(getattr(response, "status", 0)) < 300:
            continue
        payload = await _response_json(response)
        actions = payload.get("actions") if payload else None
        if not isinstance(actions, list):
            continue
        for action in actions:
            action_id = str(action.get("id") or "") if isinstance(action, Mapping) else ""
            if action_id:
                ids.append(action_id)
    return tuple(dict.fromkeys(ids))


async def run_max_functional_gate(
    bootstrap_url: str,
    *,
    require_persistence: bool,
    planned_flow: MaxPlannedFlow | None = None,
    project_id: UUID | str | None = None,
    timeout_ms: int = _TIMEOUT_MS,
) -> FunctionalVerdict:
    """Exercise one signed MAX preview and return a fail-closed verdict."""

    if planned_flow is not None:
        contract_error = _planned_flow_error(
            planned_flow,
            require_persistence=require_persistence,
        )
        if contract_error:
            return summarize([Check("max_planned_flow_contract", False, contract_error)])
        if project_id is None:
            return summarize(
                [
                    Check(
                        "max_planned_flow_contract",
                        False,
                        "project_id is required for signed integration proof",
                    )
                ]
            )
    parsed = urlsplit(bootstrap_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return summarize([Check("max_signed_session", False, "invalid bootstrap URL")])
    target_url = f"{parsed.scheme}://{parsed.netloc}/"
    checks: list[Check] = []
    try:
        from playwright.async_api import async_playwright

        from omnia_api.services.auth_session import preview_resolver_args
        from omnia_api.services.render_settle import goto_and_settle, settle

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=True,
                args=preview_resolver_args(),
            )
            try:
                context = await browser.new_context(
                    viewport={"width": 390, "height": 844},
                    reduced_motion="reduce",
                )
                if planned_flow is not None:
                    # Authenticate eager read-only integration checks on initial mount;
                    # the primary action receives a revision-scoped key before its click.
                    assert project_id is not None
                    await context.set_extra_http_headers(
                        _proof_headers(planned_flow, project_id, "__bootstrap__")
                    )
                page = await context.new_page()
                browser_errors: list[str] = []
                failed_requests: list[str] = []
                proof_infrastructure_errors: list[str] = []
                proof_owner_dependencies: list[str] = []
                managed_writes: list[tuple[Any, str | None, Mapping[str, Any] | None]] = []
                managed_integrations: list[Any] = []
                managed_reads: list[Any] = []
                expected_failure_requests: set[tuple[str, str, str]] = set()
                success_capture_active = [False]
                success_expected_signatures: set[tuple[str, str, str]] = set()
                success_request_signatures: set[tuple[str, str, str]] = set()

                def on_console(message: object) -> None:
                    if getattr(message, "type", "") == "error":
                        browser_errors.append(str(getattr(message, "text", ""))[:240])

                def on_page_error(error: object) -> None:
                    browser_errors.append(str(error)[:240])

                def on_request(request: object) -> None:
                    if not success_capture_active[0]:
                        return
                    signature = _request_proof_signature(request)
                    if signature in success_expected_signatures:
                        success_request_signatures.add(signature)

                def on_response(response: object) -> None:
                    try:
                        status = int(getattr(response, "status", 0))
                        url = str(getattr(response, "url", ""))
                        request = getattr(response, "request", None)
                        method = str(getattr(request, "method", "")).upper()
                        action_type, action_payload = _request_action_contract(request)
                        if status >= 400 and "/api/omnia/" in url:
                            if _request_proof_signature(request) in expected_failure_requests:
                                pass
                            elif _response_is_owner_dependency(response):
                                proof_owner_dependencies.append(f"HTTP {status} managed proof")
                            elif _response_is_proof_infrastructure(response):
                                proof_infrastructure_errors.append(f"HTTP {status} managed proof")
                            else:
                                failed_requests.append(f"HTTP {status} {url}"[:240])
                        if "/api/omnia/integrations/" in url:
                            managed_integrations.append(response)
                        if "/api/omnia/actions" not in url:
                            return
                        if method == "POST":
                            managed_writes.append((response, action_type, action_payload))
                        elif method == "GET":
                            managed_reads.append(response)
                    except Exception:
                        pass

                page.on("console", on_console)
                page.on("pageerror", on_page_error)
                page.on("request", on_request)
                page.on("response", on_response)
                await goto_and_settle(page, bootstrap_url, timeout_ms=timeout_ms)
                await goto_and_settle(page, target_url, timeout_ms=timeout_ms)
                if proof_owner_dependencies:
                    raise _MaxProofOwnerDependency(proof_owner_dependencies[0])
                if proof_infrastructure_errors:
                    raise _MaxProofInfrastructureUnavailable(proof_infrastructure_errors[0])

                observation = await page.evaluate(
                    """() => {
                      const visible = (el) => {
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0 && s.display !== 'none' &&
                          s.visibility !== 'hidden' && Number(s.opacity || 1) > 0;
                      };
                      const controls = [...document.querySelectorAll(
                        'button,a,[role="button"],[role="tab"]'
                      )]
                        .filter(visible);
                      const nav = [...document.querySelectorAll('[data-omnia-screen-nav]')]
                        .filter(visible);
                      const primary = [...document.querySelectorAll('[data-omnia-primary-action]')]
                        .filter(visible);
                      const unlabeled = controls.filter((el) => {
                        const text = (el.textContent || '').trim();
                        return !text && !el.getAttribute('aria-label') && !el.getAttribute('title');
                      });
                      const fake = controls.filter((el) =>
                        !['BUTTON','A'].includes(el.tagName) &&
                        (el.getAttribute('role') === 'button' || el.hasAttribute('onclick'))
                      );
                      const small = controls.filter((el) => {
                        const r = el.getBoundingClientRect();
                        return r.width < 36 || r.height < 36;
                      });
                      return {
                        nav_count: nav.length,
                        primary_count: primary.length,
                        heading_count: document.querySelectorAll(
                          'main h1,main h2,' +
                          '[data-omnia-product-runtime] h1,' +
                          '[data-omnia-product-runtime] h2'
                        ).length,
                        unlabeled_controls: unlabeled.length,
                        fake_controls: fake.length,
                        small_targets: small.length,
                        horizontal_overflow: Math.max(
                          0, document.documentElement.scrollWidth - innerWidth
                        ),
                      };
                    }"""
                )
                checks.extend(evaluate_static_observation(observation))

                visible_screen_states: list[tuple[str, ...]] = []
                navigated_screen_ids: list[str] = []
                meaningful_screen_ids: set[str] = set()
                capability_nav_indexes: dict[str, int | None] = {}
                if planned_flow is not None:
                    initial_screens = await _meaningful_visible_screen_values(page)
                    visible_screen_states.append(initial_screens)
                    meaningful_screen_ids.update(initial_screens)
                    initial_capabilities = await _active_capability_values(
                        page,
                        initial_screens,
                    )
                    for capability_id in initial_capabilities:
                        if capability_id in planned_flow.capability_ids:
                            capability_nav_indexes.setdefault(capability_id, None)

                nav = page.locator("[data-omnia-screen-nav]:visible")
                nav_count = min(await nav.count(), _MAX_NAV_CONTROLS)
                changed = 0
                for index in range(nav_count):
                    control = nav.nth(index)
                    before = await _meaningful_visible_screen_values(page)
                    try:
                        await control.click(timeout=4_000)
                        await settle(page)
                    except Exception:
                        continue
                    after = await _meaningful_visible_screen_values(page)
                    if planned_flow is not None:
                        visible_screen_states.append(after)
                        meaningful_screen_ids.update(after)
                        exact_screens = set(after).intersection(planned_flow.screen_ids)
                        if len(exact_screens) == 1:
                            navigated_screen_ids.append(next(iter(exact_screens)))
                        visible_capabilities = await _active_capability_values(
                            page,
                            after,
                        )
                        for capability_id in visible_capabilities:
                            if capability_id in planned_flow.capability_ids:
                                capability_nav_indexes.setdefault(capability_id, index)
                    selected = await control.get_attribute("aria-selected")
                    current = await control.get_attribute("aria-current")
                    if before != after or selected == "true" or bool(current):
                        changed += 1
                if proof_owner_dependencies:
                    raise _MaxProofOwnerDependency(proof_owner_dependencies[-1])
                if proof_infrastructure_errors:
                    raise _MaxProofInfrastructureUnavailable(proof_infrastructure_errors[-1])
                checks.append(
                    Check(
                        "max_navigation_interaction",
                        nav_count >= 2 and changed >= min(2, nav_count),
                        f"{changed}/{nav_count} marked view switches changed active UI",
                    )
                )

                if planned_flow is not None:
                    capability_evidence: dict[str, MaxCapabilityEvidence] = {}
                    capability_actions = dict(planned_flow.capability_actions)
                    primary_screen_transition = False
                    primary_error_ok = False
                    primary_error_detail = "primary action had no verified outcome to replay"
                    for capability_id in planned_flow.capability_ids:
                        await goto_and_settle(page, target_url, timeout_ms=timeout_ms)
                        nav_index = capability_nav_indexes.get(capability_id)
                        if nav_index is not None:
                            current_nav = page.locator("[data-omnia-screen-nav]:visible")
                            if nav_index < await current_nav.count():
                                try:
                                    await current_nav.nth(nav_index).click(timeout=4_000)
                                    await settle(page)
                                except Exception:
                                    pass
                        action, marker_count = await _visible_capability_control(
                            page,
                            capability_id,
                        )
                        if action is None or capability_id not in capability_nav_indexes:
                            capability_evidence[capability_id] = MaxCapabilityEvidence(
                                marker_count=marker_count
                            )
                            continue
                        tag_name = str(await action.evaluate("(el) => el.tagName")).upper()
                        role = str(await action.get_attribute("role") or "").casefold()
                        semantic = tag_name in {"BUTTON", "A", "INPUT"} or role in {
                            "button",
                            "checkbox",
                            "menuitem",
                            "radio",
                            "switch",
                            "tab",
                        }
                        enabled = bool(await action.is_enabled())
                        accessible_name = str(
                            await action.evaluate(
                                """(el) => (el.getAttribute('aria-label') ||
                                  el.innerText || el.textContent || el.value || '').trim()"""
                            )
                        )
                        persisted_marker = (
                            await action.get_attribute("data-omnia-persisted-action") is not None
                        )
                        primary_marker = (
                            await action.get_attribute("data-omnia-primary-action") is not None
                        )
                        action_label_match = (
                            await action.get_attribute("data-omnia-capability-label")
                            == capability_actions[capability_id]
                        )
                        accessible_name_match = _contains_action_semantics(
                            accessible_name,
                            capability_actions[capability_id],
                        )
                        structural = {
                            "marker_count": marker_count,
                            "control_ready": semantic and enabled,
                            "action_label_match": action_label_match,
                            "accessible_name_match": accessible_name_match,
                            "primary_marker": primary_marker,
                            "persisted_marker": persisted_marker,
                        }
                        if capability_id != planned_flow.primary_action_id:
                            capability_evidence[capability_id] = MaxCapabilityEvidence(**structural)
                            continue
                        action_before = await _scoped_action_result_fingerprint(page, capability_id)
                        proof_key = _proof_idempotency_key(planned_flow, capability_id)
                        assert project_id is not None
                        await context.set_extra_http_headers(
                            _proof_headers(planned_flow, project_id, capability_id)
                        )
                        await _prepare_primary_form_controls(action, proof_key[:12])
                        await settle(page)
                        primary_before_screens = await _meaningful_visible_screen_values(page)
                        before_error, before_success_count = await _scoped_action_feedback(
                            page,
                            capability_id,
                        )
                        failure_intercepted = [False]
                        failure_probe_active = [False]
                        captured_failures: set[tuple[str, str, str]] = set()
                        write_start = len(managed_writes)
                        integration_start = len(managed_integrations)
                        proof_infrastructure_start = len(proof_infrastructure_errors)
                        proof_owner_start = len(proof_owner_dependencies)

                        fail_primary_request = _primary_failure_route_handler(
                            capability_id,
                            probe_active=failure_probe_active,
                            intercepted=failure_intercepted,
                            captured=captured_failures,
                            expected_requests=expected_failure_requests,
                        )

                        failure_patterns = (
                            "**/api/omnia/actions",
                            "**/api/omnia/integrations/**",
                        )
                        for pattern in failure_patterns:
                            await page.route(pattern, fail_primary_request)
                        error_clicked = False
                        try:
                            failure_probe_active[0] = True
                            await action.click(timeout=4_000)
                            error_clicked = True
                            await settle(page)
                        except Exception:
                            pass
                        finally:
                            failure_probe_active[0] = False
                            for pattern in failure_patterns:
                                await page.unroute(pattern, fail_primary_request)
                            for expected in captured_failures:
                                expected_failure_requests.discard(expected)
                        after_error, after_error_success_count = await _scoped_action_feedback(
                            page,
                            capability_id,
                        )
                        if failure_intercepted[0]:
                            primary_error_ok = bool(
                                error_clicked
                                and after_error != "[]"
                                and after_error != before_error
                                and after_error_success_count <= before_success_count
                            )
                            primary_error_detail = (
                                "forced managed 503 produced a new scoped error without success"
                                if primary_error_ok
                                else (
                                    "forced managed 503 lacked a new scoped alert or added "
                                    "a success marker"
                                )
                            )
                            await goto_and_settle(page, target_url, timeout_ms=timeout_ms)
                            nav_index = capability_nav_indexes.get(capability_id)
                            if nav_index is not None:
                                current_nav = page.locator("[data-omnia-screen-nav]:visible")
                                if nav_index < await current_nav.count():
                                    try:
                                        await current_nav.nth(nav_index).click(timeout=4_000)
                                        await settle(page)
                                    except Exception:
                                        pass
                            action, _ = await _visible_capability_control(page, capability_id)
                            if action is not None:
                                await _prepare_primary_form_controls(action, proof_key[:12])
                                await settle(page)
                                action_before = await _scoped_action_result_fingerprint(
                                    page,
                                    capability_id,
                                )
                                primary_before_screens = (
                                    await _meaningful_visible_screen_values(page)
                                )

                        clicked = error_clicked and not failure_intercepted[0]
                        if failure_intercepted[0]:
                            clicked = False
                            if action is not None:
                                try:
                                    success_expected_signatures.clear()
                                    success_expected_signatures.update(captured_failures)
                                    success_request_signatures.clear()
                                    success_capture_active[0] = True
                                    await action.click(timeout=4_000)
                                    clicked = True
                                    await settle(page)
                                except Exception:
                                    pass
                                finally:
                                    success_capture_active[0] = False
                        if len(proof_owner_dependencies) > proof_owner_start:
                            raise _MaxProofOwnerDependency(proof_owner_dependencies[-1])
                        if len(proof_infrastructure_errors) > proof_infrastructure_start:
                            raise _MaxProofInfrastructureUnavailable(
                                proof_infrastructure_errors[-1]
                            )
                        action_writes = managed_writes[write_start:]
                        write_statuses = tuple(
                            int(getattr(response, "status", 0)) for response, _, _ in action_writes
                        )
                        causal_writes = [
                            response
                            for response, action_type, _ in action_writes
                            if action_type == capability_id
                            and _request_proof_signature(getattr(response, "request", None))
                            in success_request_signatures
                        ]
                        causal_write_statuses = tuple(
                            int(getattr(response, "status", 0)) for response in causal_writes
                        )
                        causal_integrations = [
                            response
                            for response in managed_integrations[integration_start:]
                            if _request_proof_signature(getattr(response, "request", None))
                            in success_request_signatures
                        ]
                        (
                            causal_integration_statuses,
                            causal_integration_operations,
                            causal_integration_values,
                        ) = await _integration_response_evidence(
                            causal_integrations,
                            expected_operation=planned_flow.primary_integration_operation,
                        )
                        write_ids = await _created_action_ids(causal_writes)
                        primary_after_screens = await _meaningful_visible_screen_values(page)
                        before_exact = set(primary_before_screens).intersection(
                            planned_flow.screen_ids
                        )
                        after_exact = set(primary_after_screens).intersection(
                            planned_flow.screen_ids
                        )
                        primary_screen_transition = bool(
                            len(after_exact) == 1 and after_exact != before_exact
                        )
                        action_after = (
                            await _scoped_action_result_fingerprint(page, capability_id)
                            if clicked
                            else action_before
                        )
                        if primary_screen_transition and not failure_intercepted[0]:
                            primary_error_ok = True
                            primary_error_detail = "not applicable to local screen transition"
                        capability_evidence[capability_id] = MaxCapabilityEvidence(
                            **structural,
                            clicked=clicked,
                            observable_change=clicked
                            and (
                                action_before != action_after
                                or primary_screen_transition
                                or any(200 <= status < 300 for status in causal_write_statuses)
                                or any(
                                    200 <= status < 300
                                    for status in causal_integration_statuses
                                )
                            ),
                            semantic_result=clicked and action_before != action_after,
                            managed_write_statuses=write_statuses,
                            causal_managed_write_statuses=causal_write_statuses,
                            causal_integration_statuses=causal_integration_statuses,
                            causal_integration_operations=causal_integration_operations,
                            causal_integration_values=causal_integration_values,
                            managed_write_ids=write_ids,
                            scoped_result_semantics=_scoped_result_semantics(action_after),
                        )

                    reload_read_statuses: tuple[int, ...] = ()
                    reloaded_action_ids: tuple[str, ...] = ()
                    persistence_ui_restored = False
                    persistence_id = planned_flow.persistence_action_id
                    persistence = capability_evidence.get(persistence_id or "")
                    if (
                        require_persistence
                        and persistence
                        and any(
                            200 <= status < 300
                            for status in persistence.causal_managed_write_statuses
                        )
                    ):
                        read_start = len(managed_reads)
                        await goto_and_settle(page, target_url, timeout_ms=timeout_ms)
                        restored_record_ids = set(await _visible_record_ids(page))
                        restored_nav = page.locator("[data-omnia-screen-nav]:visible")
                        restored_nav_count = min(
                            await restored_nav.count(),
                            _MAX_NAV_CONTROLS,
                        )
                        for index in range(restored_nav_count):
                            try:
                                await restored_nav.nth(index).click(timeout=4_000)
                                await settle(page)
                            except Exception:
                                continue
                            restored_record_ids.update(await _visible_record_ids(page))
                        reload_reads = managed_reads[read_start:]
                        reload_read_statuses = tuple(
                            int(getattr(response, "status", 0)) for response in reload_reads
                        )
                        reloaded_action_ids = await _listed_action_ids(reload_reads)
                        persistence_ui_restored = bool(
                            restored_record_ids.intersection(persistence.managed_write_ids)
                        )
                    checks.extend(
                        evaluate_planned_flow_evidence(
                            planned_flow,
                            MaxPlannedFlowEvidence(
                                visible_screen_states=tuple(visible_screen_states),
                                navigated_screen_ids=tuple(navigated_screen_ids),
                                meaningful_screen_ids=tuple(meaningful_screen_ids),
                                capabilities=capability_evidence,
                                primary_screen_transition=primary_screen_transition,
                                reload_read_statuses=reload_read_statuses,
                                reloaded_action_ids=reloaded_action_ids,
                                persistence_ui_restored=persistence_ui_restored,
                            ),
                            require_persistence=require_persistence,
                        )
                    )
                    checks.append(
                        Check(
                            "max_primary_error_handling",
                            primary_error_ok,
                            primary_error_detail,
                        )
                    )
                else:
                    action_selector = (
                        "[data-omnia-persisted-action]:visible"
                        if require_persistence
                        else "[data-omnia-primary-action]:visible"
                    )
                    actions = page.locator(action_selector)
                    action_count = await actions.count()
                    action_changed = False
                    write_start = len(managed_writes)
                    if action_count:
                        before_text = await page.locator(
                            "[data-omnia-product-runtime]"
                        ).inner_text()
                        try:
                            await actions.first.click(timeout=4_000)
                            await settle(page)
                            after_text = await page.locator(
                                "[data-omnia-product-runtime]"
                            ).inner_text()
                            legacy_write_statuses = [
                                int(getattr(response, "status", 0))
                                for response, _, _ in managed_writes[write_start:]
                            ]
                            action_changed = before_text != after_text or any(
                                200 <= status < 300 for status in legacy_write_statuses
                            )
                        except Exception:
                            action_changed = False
                    checks.append(
                        Check(
                            "max_primary_action_interaction",
                            action_count > 0 and action_changed,
                            "marked action changed UI or completed a managed write"
                            if action_changed
                            else "marked action was missing or produced no observable result",
                        )
                    )

                    if require_persistence:
                        writes = [
                            int(getattr(response, "status", 0))
                            for response, _, _ in managed_writes[write_start:]
                        ]
                        write_ok = any(200 <= status < 300 for status in writes)
                        read_start = len(managed_reads)
                        if write_ok:
                            await goto_and_settle(page, target_url, timeout_ms=timeout_ms)
                        reads = [
                            int(getattr(response, "status", 0))
                            for response in managed_reads[read_start:]
                        ]
                        read_ok = any(200 <= status < 300 for status in reads)
                        checks.append(
                            Check(
                                "max_reload_persistence",
                                write_ok and read_ok,
                                (
                                    f"managed write statuses={writes or 'none'}, "
                                    f"reload reads={reads or 'none'}"
                                ),
                            )
                        )

                if proof_owner_dependencies:
                    raise _MaxProofOwnerDependency(proof_owner_dependencies[-1])
                if proof_infrastructure_errors:
                    raise _MaxProofInfrastructureUnavailable(proof_infrastructure_errors[-1])
                checks.append(
                    Check(
                        "max_browser_errors",
                        not browser_errors and not failed_requests,
                        (
                            "no console/page/managed-request errors"
                            if not browser_errors and not failed_requests
                            else "; ".join((browser_errors + failed_requests)[:6])
                        ),
                    )
                )
            finally:
                await browser.close()
    except _MaxProofOwnerDependency as exc:
        checks.append(
            Check(
                "max_signed_session",
                False,
                f"owner dependency: {exc}",
            )
        )
    except Exception as exc:
        checks.append(
            Check(
                "max_signed_session",
                False,
                f"infrastructure unavailable: {type(exc).__name__}",
            )
        )
    return summarize(checks)


__all__ = [
    "MaxCapabilityEvidence",
    "MaxPlannedFlow",
    "MaxPlannedFlowEvidence",
    "evaluate_planned_flow_evidence",
    "evaluate_static_observation",
    "run_max_functional_gate",
]
