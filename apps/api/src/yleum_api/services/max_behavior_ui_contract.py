"""Platform-owned named UI interfaces; never inferred from customer markup."""

from types import MappingProxyType

from yleum_api.services.max_behavior_proof import NamedBehaviorContract

COFFEE_UI_PRESET = "coffee-local-summary-ui-v1"
THEME_UI_PRESET = "header-theme-ui-v1"
COFFEE_THEME_UI_PRESET = "coffee-summary-header-theme-ui-v1"
DENSITY_STORAGE_KEY = "omnia:task-density:v1"
DENSITY_FILTER_VALUE = "open"
DENSITY_SELECTORS = MappingProxyType(
    {
        "normal": '[data-omnia-behavior="density-normal"]',
        "compact": '[data-omnia-behavior="density-compact"]',
        "filter": '[data-omnia-behavior="density-filter"]',
        "controls": '[data-omnia-density-ui] :is(button,select,[role="button"])',
    }
)
DENSITY_LIST_VIEW = MappingProxyType(
    {
        "control": '[data-omnia-behavior="density-list"]',
        "container": '[data-omnia-behavior="density-list-items"]',
        "cards": '[data-omnia-behavior="density-list-items"] [data-item-id]',
    }
)
DENSITY_BOARD_VIEW = MappingProxyType(
    {
        "control": '[data-omnia-behavior="density-board"]',
        "container": '[data-omnia-behavior="density-board-items"]',
        "cards": '[data-omnia-behavior="density-board-items"] [data-item-id]',
    }
)
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
    if "task_density_v1" in contract.capabilities:
        controls = "\n".join(f"- {key}: {selector}" for key, selector in DENSITY_SELECTORS.items())
        views = "\n".join(
            f"- {view} {key}: {selector}"
            for view, selectors in (("list", DENSITY_LIST_VIEW), ("board", DENSITY_BOARD_VIEW))
            for key, selector in selectors.items()
        )
        parts.append(
            "PLATFORM TASK DENSITY UI CONTRACT v1: Wire the actual task list and board to "
            "the following platform-owned selectors. Place the task controls and both views "
            "inside the task region marked data-omnia-density-ui.\n"
            f"{controls}\n{views}\n"
            "Expose visible, accessible buttons for the normal and compact modes. Preserve "
            "the owner's requested accessible labels. Use a real native status "
            f"select with values 'all' and '{DENSITY_FILTER_VALUE}'. In each view mark the "
            "rendered task cards with their stable task identities as data-item-id. Keep "
            "the same tasks, text, order and active filter while switching density. "
            "In both views Compact must reduce each card's painted height and top padding "
            "and the container's CSS row gap. Use measurable pixel spacing and keep the "
            "active view within the viewport width. All visible task controls must remain "
            "at least 44 by 44 CSS pixels on desktop and mobile. Persist the selected "
            f"'normal' or 'compact' in localStorage key '{DENSITY_STORAGE_KEY}'; restore "
            "Compact after reload and return to the original geometry when selecting Normal. "
            "Use the real authenticated screen and preserve existing records. The controller "
            "measures the candidate with its own browser before promotion."
        )
    if "coffee_local_summary_v1" in contract.capabilities:
        parts.append(
            "PLATFORM COFFEE UI CONTRACT v1: Implement the requested local summary "
            "in the actual product screen using these platform-owned interface attributes:\n"
            + "\n".join(f"- {key}: {selector}" for key, selector in COFFEE_SELECTORS.items())
            + '\nGive the visible control the requested accessible name and type="button". '
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
            + '\nPlace visible type="button" controls for light and dark mode inside the header '
            "and preserve the owner's requested accessible labels. Each control must be at "
            "least 44 by 44 CSS pixels "
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
    return "\n\n".join(parts)
