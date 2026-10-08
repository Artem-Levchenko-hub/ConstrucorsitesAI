"""Platform-owned named UI interfaces; never inferred from customer markup."""

from types import MappingProxyType

from yleum_api.services.max_behavior_proof import NamedBehaviorContract

COFFEE_UI_PRESET = "coffee-local-summary-ui-v1"
THEME_UI_PRESET = "header-theme-ui-v1"
COFFEE_THEME_UI_PRESET = "coffee-summary-header-theme-ui-v1"
THEME_STORAGE_KEY = "omnia:theme:v1"
THEME_SELECTORS = MappingProxyType(
    {
        "header": '[data-omnia-behavior="theme-header"]',
        "light": '[data-omnia-behavior="theme-light"]',
        "dark": '[data-omnia-behavior="theme-dark"]',
        "card": '[data-omnia-behavior="theme-card"]',
        "text": '[data-omnia-behavior="theme-text"]',
    }
)
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
    if "header_theme_v1" in contract.capabilities:
        parts.append(
            "PLATFORM THEME UI CONTRACT v1: Connect the requested theme to the actual "
            "product screen with these unique platform-owned interface attributes:\n"
            + "\n".join(f"- {key}: {selector}" for key, selector in THEME_SELECTORS.items())
            + '\nPlace visible type="button" controls with exact accessible names Light and Dark '
            "inside the visible header. Each control must be at least 44 by 44 CSS pixels "
            "at desktop and mobile widths. Select one real visible content card and its text. "
            "Both themes must actually change the computed painted body/card backgrounds "
            "and text colors: light body/card luminance >= 0.75, dark <= 0.18, with card/text "
            "contrast >= 4.5 in both modes. Use solid opaque colors for these measured surfaces. "
            "Store the selected 'light' or 'dark' value in localStorage key "
            f"'{THEME_STORAGE_KEY}'. "
            "Dark must survive reload with the same painted colors; switching back to Light "
            "must be reversible. Preserve existing data, forms and authentication. "
            "No business/provider writes. Source attributes alone do not prove behavior: "
            "candidate-bound browser measurements are required before promotion."
        )
    unsupported = set(contract.capabilities) - {"coffee_local_summary_v1", "header_theme_v1"}
    if unsupported:
        parts.append(
            "PLATFORM NAMED UI ADAPTER UNSUPPORTED: "
            + ", ".join(sorted(unsupported))
            + ". The Coffee and theme presets do not supply density selectors or storage keys. "
            "Do not invent an operator mapping or claim behavioral PASS for those capabilities."
        )
    return "\n\n".join(parts)
