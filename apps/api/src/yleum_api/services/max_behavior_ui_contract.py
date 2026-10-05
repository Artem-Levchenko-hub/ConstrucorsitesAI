"""Shared prospective Coffee interface; never inferred from customer markup."""

from types import MappingProxyType

from yleum_api.services.max_behavior_proof import NamedBehaviorContract

COFFEE_UI_PRESET = "coffee-local-summary-ui-v1"
COFFEE_SELECTORS = MappingProxyType(
    {
        "button": '[data-omnia-behavior="coffee-summary-button"]',
        "form": '[data-omnia-behavior="coffee-request-form"]',
        "text_input": '[data-omnia-behavior="coffee-request-text"]',
        "summary": '[data-omnia-behavior="coffee-request-summary"]',
    }
)


def named_ui_guidance(contract: NamedBehaviorContract | None) -> str:
    if contract is None:
        return ""
    parts = []
    if "coffee_local_summary_v1" in contract.capabilities:
        parts.append(
            "PLATFORM COFFEE UI CONTRACT v1: Implement the requested local summary "
            "in the actual product screen using these platform-owned interface attributes:\n"
            + "\n".join(f"- {key}: {selector}" for key, selector in COFFEE_SELECTORS.items())
            + '\nThe painted button has exact accessible name «Проверить заявку» '
            'and type="button". '
            "The form contains the relevant existing visible text input or textarea. "
            "Clicking updates the painted summary from current input "
            "and preserves all form values; "
            "this is a local state change: do not send business/provider requests "
            "or submit the form. "
            "No synthetic user records. Preserve existing UI and authentication. "
            "Actual candidate-bound browser measurements and compiled assets are required "
            "before promotion; comments, source literals or model claims cannot prove PASS. "
            "Unavailable controller browser/adapter means NEEDS_REVIEW; never fabricate receipts."
        )
    unsupported = set(contract.capabilities) - {"coffee_local_summary_v1"}
    if unsupported:
        parts.append(
            "PLATFORM NAMED UI ADAPTER UNSUPPORTED: "
            + ", ".join(sorted(unsupported))
            + ". The Coffee preset does not supply density/theme selectors or storage keys. "
            "Do not invent an operator mapping or claim behavioral PASS for those capabilities."
        )
    return "\n\n".join(parts)
