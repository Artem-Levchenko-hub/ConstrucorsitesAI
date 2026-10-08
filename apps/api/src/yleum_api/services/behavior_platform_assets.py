"""Exact operator-owned assets allowed outside the candidate's Next compilation."""

MAX_SDK_URL = "https://st.max.ru/js/max-web-app.js"
FIXED_PLATFORM_ASSET_PATHS = frozenset({
    "/_omnia/inspector.js",
    "/omnia-inspector.js",
    "/omnia-remix-cta.js",
    "/omnia-brief-narration.js",
})
PLATFORM_ASSET_LOCATIONS = FIXED_PLATFORM_ASSET_PATHS | {MAX_SDK_URL}
