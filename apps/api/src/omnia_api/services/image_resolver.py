"""Post-generation image-resolver.

Scans AI-generated files for ``<img data-omnia-gen="prompt"... />`` tags,
generates real images via the LLM gateway's ``/v1/images/generations``
endpoint (gpt-image-1, low quality), uploads them to MinIO and rewrites the
tag with a public ``<img src="...">``.

Single point of contact for the image-gen pipeline (R-01 deep module): callers
hand over a ``files`` dict and a ``project_id`` and get back the same dict with
tags replaced. Failure modes (gateway 5xx, MinIO outage, malformed b64) leave
the offending tag untouched so a partial outage doesn't destroy the page.

Budget guard: ``MAX_IMAGES_PER_RESOLVE`` caps how many tags we honour per
prompt (extras are left as-is). Same prompt within one resolve dedupes to a
single gateway call + a single MinIO object — Haiku repeating
"гамбургер крупным планом" five times costs us one image.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from io import BytesIO

import httpx
from minio.error import S3Error

from omnia_api.core.config import get_settings, model_for_role
from omnia_api.core.minio import get_minio_client

log = logging.getLogger(__name__)

# Image-prompt enrichment (Phase N+, role `image_prompt`). Prompts with at
# least this many words are already detailed (the generator is told to write
# "subject, scene, style, lighting, angle, lens") and skip enrichment — only
# short/weak prompts get expanded. Keeps the role honest without taxing the
# common case.
_ENRICH_MIN_WORDS = 8
_ENRICH_SYSTEM = (
    "You are a senior art-buyer writing prompts for a premium image generator. "
    "Expand the short description into ONE vivid English prompt for a high-end, "
    "photorealistic, editorial-grade image: name the subject, scene, composition, "
    "lighting, mood, lens and color grade. Favor cinematic, magazine-quality, "
    "razor-sharp results with depth and intentional negative space (it will sit "
    "behind UI). No text, no logos, no watermarks. Return ONLY the prompt text — "
    "no quotes, no preamble, no commentary."
)

# Hard ceiling per generation. Beyond this we log a warning and leave extra
# tags untouched (they render as broken images — visible signal to the user
# without burning the wallet).
# Quality-over-quantity: flux-2-pro is ~13s/image, so cap at a hero + a handful
# of section backgrounds. Fewer, gorgeous, fully-rendered images beat a dozen
# half-stripped at the budget. Raise if you switch to a faster model.
MAX_IMAGES_PER_RESOLVE = 8

# Concurrent gateway calls. The vsegpt image key rate-limits to ~1 request/sec,
# so fan-out bursts 429. Serial (=1) keeps us under the limit — each gen takes
# ~10-30s anyway, so requests are naturally spaced well over 1s apart.
_CONCURRENCY = 1

# Per-image timeout (sec) — slightly over the gateway's internal 60s ceiling
# so client-side aborts come from us, not from the upstream.
_REQUEST_TIMEOUT = 75.0

# Overall wall-clock budget for the AI image-gen phase. Even with the per-image
# timeout above, N unresolved tags × 75s serialised behind _CONCURRENCY would
# stall the whole build for minutes (no snapshot → the preview loads forever).
# When this budget is hit we cancel the in-flight gen calls and strip the
# remaining tags so the build always ships (R-10: bound every wait, fail fast).
# Sized for serial real gens (≤12 imgs × ~10-15s); stragglers past it are
# stripped, never hung. A dead upstream still can't block longer than this.
_IMAGE_RESOLVE_BUDGET_S = 180.0

# File types we scan. JSX/TSX cover Next.js fullstack templates; html/htm
# covers static templates.
_SCAN_EXTS = (
    ".html",
    ".htm",
    ".tsx",
    ".jsx",
    ".ts",
    ".js",
    ".vue",
    ".astro",
    ".svelte",
)

# Tag regex — matches:
#   <img ... data-omnia-gen="prompt" ... />
#   <img ... data-omnia-gen="prompt" ... >
# Captures the prompt and the attrs on either side so we can preserve them
# (alt, class, width, height, etc.) in the rewritten tag.
_TAG_RE = re.compile(
    r'<img\b([^>]*?)\bdata-omnia-gen\s*=\s*"([^"]+)"([^>]*?)/?\s*>',
    re.IGNORECASE | re.DOTALL,
)

# Stock-photo tag — sibling of data-omnia-gen. The "prompt" here is a short
# keyword phrase ("sushi restaurant interior") searched against Pexels.
_TAG_RE_PHOTO = re.compile(
    r'<img\b([^>]*?)\bdata-omnia-photo\s*=\s*"([^"]+)"([^>]*?)/?\s*>',
    re.IGNORECASE | re.DOTALL,
)

# Optional sibling of data-omnia-gen. Every tag carrying the SAME group value
# collapses to ONE generation (the group's first prompt), reused across all of
# them — product / menu / portfolio cards cost one image instead of N.
_GROUP_RE = re.compile(r'data-omnia-gen-group\s*=\s*"([^"]+)"', re.IGNORECASE)

# Pexels search endpoint. Landscape orientation suits full-bleed section
# backgrounds; per_page gives a small deterministic pick-pool per keyword.
_PEXELS_SEARCH_URL = "https://api.pexels.com/v1/search"
_PEXELS_PER_PAGE = 15

# ── Openverse stock (free CC0 / Public Domain) ────────────────────────────
# api.openverse.org is reachable from the RU prod egress where Pexels/Unsplash
# 404/000. Anonymous search works; an optional OAuth client-credentials token
# lifts the rate limit. Image bytes come from the source CDN, with the Openverse
# thumbnail proxy (same reachable host) as the guaranteed-reachable fallback.
_OPENVERSE_SEARCH_URL = "https://api.openverse.org/v1/images/"
_OPENVERSE_TOKEN_URL = "https://api.openverse.org/v1/auth_tokens/token/"
_OPENVERSE_PAGE_SIZE = 12

# Cached client-credentials token (Openverse tokens last ~12h). Refreshed when
# within 60s of expiry; the lock stops concurrent resolves stampeding the token
# endpoint. monotonic clock so a wall-clock jump can't expire it early.
_ov_token: dict[str, object] = {"value": None, "expires_at": 0.0}
_ov_token_lock = asyncio.Lock()


@dataclass(frozen=True)
class ImgTag:
    file_path: str
    full_match: str
    prompt: str
    pre_attrs: str
    post_attrs: str
    # Optional data-omnia-gen-group value. Tags sharing a group collapse to ONE
    # generation (the group's first prompt) — cards reuse one image (cost cut).
    group: str | None = None


def extract_image_tags(files: dict[str, str]) -> list[ImgTag]:
    """Find every ``data-omnia-gen`` tag across the given files.

    Extracts ALL tags (the budget now caps GENERATIONS, not extraction, so
    over-budget tags reuse an already-made image instead of shipping a broken
    ``<img>``). Each tag's optional ``data-omnia-gen-group`` is captured so a
    group of cards collapses to one generation downstream. Files outside
    ``_SCAN_EXTS`` are skipped (saves a regex run on css/json/md).
    """
    out: list[ImgTag] = []
    for path, content in files.items():
        if not path.lower().endswith(_SCAN_EXTS):
            continue
        for m in _TAG_RE.finditer(content):
            gm = _GROUP_RE.search(f"{m.group(1)} {m.group(3)}")
            out.append(
                ImgTag(
                    file_path=path,
                    full_match=m.group(0),
                    prompt=m.group(2).strip(),
                    pre_attrs=m.group(1),
                    post_attrs=m.group(3),
                    group=gm.group(1).strip() if gm else None,
                )
            )
    return out


async def _fetch_one(prompt: str) -> bytes | None:
    """Call gateway POST /v1/images/generations for one prompt. Returns bytes
    or None on any failure (we never raise — partial success is OK)."""
    settings = get_settings()
    url = f"{settings.llm_gateway_url.rstrip('/')}/v1/images/generations"
    payload = {
        "model": settings.image_gen_model,
        "prompt": prompt,
        "n": 1,
        "size": "1024x1024",
        "quality": "low",
    }
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            resp = await client.post(url, json=payload)
    except Exception as exc:  # noqa: BLE001 — never break the resolve over one tag
        log.warning(
            "image_resolver: gateway transport error prompt=%.40s err=%r",
            prompt,
            exc,
        )
        return None
    if resp.status_code >= 400:
        log.warning(
            "image_resolver: gateway %d prompt=%.40s body=%.200s",
            resp.status_code,
            prompt,
            resp.text,
        )
        return None
    try:
        body = resp.json()
    except ValueError:
        log.warning("image_resolver: non-JSON body prompt=%.40s", prompt)
        return None
    data = body.get("data") or []
    if not data:
        return None
    entry = data[0]
    b64 = entry.get("b64_json")
    if b64:
        try:
            return base64.b64decode(b64)
        except Exception as exc:  # noqa: BLE001
            log.warning("image_resolver: b64 decode failed err=%r", exc)
            return None
    # OpenAI sometimes returns a hosted URL instead. Fetch it.
    img_url = entry.get("url")
    if img_url:
        try:
            async with httpx.AsyncClient(timeout=30.0) as c:
                img_resp = await c.get(img_url)
            if img_resp.status_code == 200:
                return img_resp.content
            log.warning(
                "image_resolver: url-fetch %d url=%.80s",
                img_resp.status_code,
                img_url,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("image_resolver: url-fetch transport err=%r", exc)
    return None


async def _enrich_prompt(prompt: str) -> str:
    """Expand a short/weak prompt into a detailed photo brief via the
    ``image_prompt`` role. Fail-soft: returns the ORIGINAL prompt on any
    error, empty reply, or non-2xx — enrichment must never block a generation.
    """
    settings = get_settings()
    url = f"{settings.llm_gateway_url.rstrip('/')}/v1/chat/completions"
    payload = {
        "model": model_for_role("image_prompt"),
        "messages": [
            {"role": "system", "content": _ENRICH_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 220,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload)
        if resp.status_code >= 400:
            return prompt
        body = resp.json()
        text = (body.get("choices") or [{}])[0].get("message", {}).get("content", "")
        text = (text or "").strip()
        return text or prompt
    except Exception as exc:  # noqa: BLE001 — never break a resolve over enrichment
        log.warning(
            "image_resolver: prompt enrich failed prompt=%.40s err=%r", prompt, exc
        )
        return prompt


def _ensure_bucket(client, bucket: str) -> bool:
    """Idempotent bucket creation. Returns True on success or already-exists,
    False on permission/transport error (we then skip uploads gracefully)."""
    try:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
        return True
    except S3Error as exc:
        log.warning("image_resolver: bucket-ensure failed %s err=%r", bucket, exc)
        return False


def _upload_image(image_bytes: bytes, project_id: str, prompt: str) -> str | None:
    """Upload PNG to MinIO. Key is content-addressed by (project_id, prompt)
    so identical prompts within one project share one object."""
    settings = get_settings()
    client = get_minio_client()
    bucket = settings.minio_bucket_images
    if not _ensure_bucket(client, bucket):
        return None
    sha = hashlib.sha256(
        f"{project_id}|{prompt}".encode("utf-8")
    ).hexdigest()[:32]
    key = f"{project_id}/{sha}.png"
    try:
        client.put_object(
            bucket,
            key,
            BytesIO(image_bytes),
            length=len(image_bytes),
            content_type="image/png",
        )
    except S3Error as exc:
        log.warning("image_resolver: upload failed key=%s err=%r", key, exc)
        return None
    base = settings.minio_public_url.rstrip("/")
    return f"{base}/{bucket}/{key}"


def _cached_image_url(project_id: str, prompt: str) -> str | None:
    """Public URL of an ALREADY-generated image for (project_id, prompt), or
    None. Same content-addressed key as ``_upload_image``. Lets an
    acceptance/design-judge repair re-roll — and a re-generation of the same
    site — REUSE the existing MinIO object instead of paying vsegpt for an
    identical image again (the main image-cost leak). Fail-soft: any error or a
    missing object → None, so the caller just generates as before."""
    settings = get_settings()
    try:
        client = get_minio_client()
        bucket = settings.minio_bucket_images
        sha = hashlib.sha256(
            f"{project_id}|{prompt}".encode("utf-8")
        ).hexdigest()[:32]
        key = f"{project_id}/{sha}.png"
        client.stat_object(bucket, key)  # raises if the object is absent
    except Exception:  # noqa: BLE001 — absent/transport error → regenerate
        return None
    base = settings.minio_public_url.rstrip("/")
    return f"{base}/{bucket}/{key}"


def _extract_photo_tags(files: dict[str, str]) -> list[ImgTag]:
    """Find every ``data-omnia-photo`` tag (capped like the gen path)."""
    out: list[ImgTag] = []
    for path, content in files.items():
        if not path.lower().endswith(_SCAN_EXTS):
            continue
        for m in _TAG_RE_PHOTO.finditer(content):
            out.append(
                ImgTag(
                    file_path=path,
                    full_match=m.group(0),
                    prompt=m.group(2).strip(),
                    pre_attrs=m.group(1),
                    post_attrs=m.group(3),
                )
            )
            if len(out) >= MAX_IMAGES_PER_RESOLVE:
                return out
    return out


def _pexels_client_kwargs() -> dict:
    """httpx.AsyncClient kwargs for Pexels calls — routes through the egress
    proxy when ``settings.pexels_proxy`` is set (pexels.com is unreliable from
    the RU prod egress). Pexels only; the gpt-image path (``_fetch_one``) is
    untouched. httpx 0.28 uses the singular ``proxy=`` argument."""
    kw: dict = {"timeout": _REQUEST_TIMEOUT}
    proxy = get_settings().pexels_proxy
    if proxy:
        kw["proxy"] = proxy
    return kw


async def _fetch_photo(keywords: str, project_id: str) -> bytes | None:
    """Search Pexels for ``keywords`` and download one photo. Deterministic
    pick by (project_id, keywords) so the same project re-renders the same
    photo, while different projects get different shots for the same keyword.
    Returns bytes or None on any failure (never raises — fail-soft)."""
    settings = get_settings()
    key = settings.pexels_api_key
    if settings.photo_source != "pexels" or key is None:
        return None
    headers = {"Authorization": key.get_secret_value()}
    params = {"query": keywords, "per_page": _PEXELS_PER_PAGE, "orientation": "landscape"}
    try:
        async with httpx.AsyncClient(**_pexels_client_kwargs()) as client:
            resp = await client.get(_PEXELS_SEARCH_URL, params=params, headers=headers)
    except Exception as exc:  # noqa: BLE001 — never break a resolve over one tag
        log.warning("image_resolver: pexels transport error kw=%.40s err=%r", keywords, exc)
        return None
    if resp.status_code >= 400:
        log.warning("image_resolver: pexels %d kw=%.40s", resp.status_code, keywords)
        return None
    try:
        photos = resp.json().get("photos") or []
    except ValueError:
        return None
    if not photos:
        return None
    pick = int(
        hashlib.sha256(f"{project_id}|{keywords}".encode("utf-8")).hexdigest(), 16
    ) % len(photos)
    src = photos[pick].get("src") or {}
    img_url = src.get("large2x") or src.get("large") or src.get("original")
    if not img_url:
        return None
    try:
        async with httpx.AsyncClient(**_pexels_client_kwargs()) as c:
            img_resp = await c.get(img_url)
        if img_resp.status_code == 200:
            return img_resp.content
        log.warning("image_resolver: pexels img-fetch %d", img_resp.status_code)
    except Exception as exc:  # noqa: BLE001
        log.warning("image_resolver: pexels img-fetch transport err=%r", exc)
    return None


async def _openverse_token() -> str | None:
    """OAuth client-credentials bearer token for Openverse, cached until ~expiry.

    Returns None when no client creds are configured — anonymous search still
    works (just rate-limited), so a missing token degrades, never breaks. Any
    transport/parse error also returns None (fall back to anonymous)."""
    settings = get_settings()
    cid = settings.openverse_client_id
    secret = settings.openverse_client_secret
    if not cid or secret is None:
        return None
    if _ov_token["value"] and time.monotonic() < float(_ov_token["expires_at"]):
        return _ov_token["value"]  # type: ignore[return-value]
    async with _ov_token_lock:
        if _ov_token["value"] and time.monotonic() < float(_ov_token["expires_at"]):
            return _ov_token["value"]  # type: ignore[return-value]
        data = {
            "grant_type": "client_credentials",
            "client_id": cid,
            "client_secret": secret.get_secret_value(),
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(_OPENVERSE_TOKEN_URL, data=data)
            if resp.status_code >= 400:
                log.warning("image_resolver: openverse token %d", resp.status_code)
                return None
            body = resp.json()
        except Exception as exc:  # noqa: BLE001 — anonymous fallback on any failure
            log.warning("image_resolver: openverse token err=%r", exc)
            return None
        token = body.get("access_token")
        ttl = float(body.get("expires_in") or 0)
        if not token:
            return None
        _ov_token["value"] = token
        _ov_token["expires_at"] = time.monotonic() + max(0.0, ttl - 60)
        return token


def _openverse_query_variants(keywords: str) -> list[str]:
    """Progressively broader search queries (≤3). The CC0/Public-Domain pool is
    sparse for long descriptive phrases ('modern dental clinic interior' → 0 hits),
    so fall back to a shorter THEME-ANCHORED phrase ('dental clinic', 'clinic
    interior'). Never drop to a single generic word ('interior', 'portrait') — that
    pulls off-theme junk; keep at least two words so the theme survives."""
    kw = keywords.strip()
    head = kw.split(",")[0].strip()
    words = [w for w in re.split(r"\s+", head) if w]
    candidates = [kw, head]
    if len(words) > 2:
        candidates.append(" ".join(words[:2]))   # leading words = the subject
        candidates.append(" ".join(words[-2:]))  # trailing words = the object
    out: list[str] = []
    seen: set[str] = set()
    for c in candidates:
        if c and c.lower() not in seen:
            seen.add(c.lower())
            out.append(c)
    return out[:3]


# Words too generic to anchor relevance — they sit in countless off-theme titles.
_OV_STOPWORDS = frozenset({
    "the", "and", "with", "for", "modern", "interior", "background", "photo",
    "image", "view", "scene", "closeup", "high", "quality", "professional", "real",
})


def _ov_relevance(item: dict, query_words: set[str]) -> int:
    """How many distinct query words appear in the item's title/tags. Openverse
    sorts by its own relevance, but the CC0 pool is thin enough that result #0 is
    often only loosely related — re-ranking against the ACTUAL keywords stops a
    'dentist portrait' query landing on a Tudor painting or a bee macro."""
    tags = item.get("tags") or []
    text = (
        (item.get("title") or "")
        + " "
        + " ".join(t.get("name", "") for t in tags if isinstance(t, dict))
    ).lower()
    return sum(1 for w in query_words if w in text)


async def _fetch_photo_openverse(keywords: str, project_id: str) -> bytes | None:
    """Search Openverse (PHOTOGRAPHS only, no-attribution licenses) and download the
    photo that BEST matches ``keywords``. The query is broadened
    (``_openverse_query_variants``) and every candidate is re-ranked by keyword
    overlap (``_ov_relevance``) — the highest-scoring photo wins. If NOTHING shares a
    keyword we return None so the tag is stripped and the section's kit graphic
    shows: a clean blank section beats a random off-theme image (owner 2026-06-07 —
    the CC0 pool served a bee for a dental hero and a Tudor painting for a surgeon).
    Bytes come from the source CDN with the Openverse thumbnail proxy as the
    reachable fallback. Fail-soft → None. (``project_id`` kept for call-site parity.)"""
    settings = get_settings()
    headers: dict[str, str] = {}
    token = await _openverse_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    query_words = {
        w
        for w in re.findall(r"[a-zа-яё0-9]+", keywords.lower())
        if len(w) > 2 and w not in _OV_STOPWORDS
    }

    async def _search(client: httpx.AsyncClient, query: str) -> list[dict]:
        params = {
            "q": query,
            "license": settings.openverse_license,
            "category": "photograph",  # exclude paintings / illustrations / clipart
            "page_size": _OPENVERSE_PAGE_SIZE,
            "mature": "false",
        }
        try:
            resp = await client.get(
                _OPENVERSE_SEARCH_URL, params=params, headers=headers
            )
        except Exception as exc:  # noqa: BLE001 — never break a resolve over one tag
            log.warning("image_resolver: openverse transport q=%.40s err=%r", query, exc)
            return []
        if resp.status_code >= 400:
            log.warning("image_resolver: openverse %d q=%.40s", resp.status_code, query)
            return []
        try:
            return resp.json().get("results") or []
        except ValueError:
            return []

    # Collect candidates across broadening steps, keep the most ON-THEME one.
    best: dict | None = None
    best_score = 0
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        for query in _openverse_query_variants(keywords):
            for item in await _search(client, query):
                score = _ov_relevance(item, query_words) if query_words else 1
                if score > best_score:  # strictly better wins; ties keep first (rank)
                    best, best_score = item, score
            # Stop once we have a solid match (2+ words, or 1 word for short queries).
            if best_score >= 2 or (best_score >= 1 and len(query_words) <= 2):
                break
    # No photo shares a keyword with the request → don't ship a random one.
    if best is None or best_score < 1:
        log.info("image_resolver: openverse no on-theme match kw=%.50s", keywords)
        return None
    chosen = best
    # Full-res source URL first (no auth header to a 3rd-party CDN); then the
    # Openverse thumbnail proxy (auth ok — same host as the API).
    # Many source CDNs (and Openverse's media proxy) 403 the default python-httpx
    # User-Agent. Send a browser UA + Accept so the bytes come through, and follow
    # redirects (the thumbnail proxy can 302 to the CDN). This was the cause of
    # "openverse img-fetch 403" stripping content photos → broken section layout.
    _ua = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/png,image/*,*/*;q=0.8",
    }
    for img_url, hdrs in ((chosen.get("url"), {}), (chosen.get("thumbnail"), headers)):
        if not img_url:
            continue
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as c:
                img_resp = await c.get(img_url, headers={**_ua, **hdrs})
            if img_resp.status_code == 200 and img_resp.content:
                return img_resp.content
            log.warning("image_resolver: openverse img-fetch %d", img_resp.status_code)
        except Exception as exc:  # noqa: BLE001
            log.warning("image_resolver: openverse img-fetch err=%r", exc)
    return None


async def _fetch_stock_photo(keywords: str, project_id: str) -> bytes | None:
    """Dispatch a stock-photo fetch to the configured provider (R-01: callers
    don't know which backend served the bytes). Returns None for "off"."""
    source = get_settings().photo_source
    if source == "pexels":
        return await _fetch_photo(keywords, project_id)
    if source == "openverse":
        return await _fetch_photo_openverse(keywords, project_id)
    return None


def _upload_photo(image_bytes: bytes, project_id: str, keywords: str) -> str | None:
    """Cache a Pexels photo in MinIO, content-addressed by (project_id,
    keywords) so the same keyword in one project costs one fetch + one object."""
    settings = get_settings()
    client = get_minio_client()
    bucket = settings.minio_bucket_photos
    if not _ensure_bucket(client, bucket):
        return None
    sha = hashlib.sha256(
        f"{settings.photo_source}|{project_id}|{keywords}".encode("utf-8")
    ).hexdigest()[:32]
    key = f"{project_id}/{sha}.jpg"
    try:
        client.put_object(
            bucket, key, BytesIO(image_bytes), length=len(image_bytes),
            content_type="image/jpeg",
        )
    except S3Error as exc:
        log.warning("image_resolver: photo upload failed key=%s err=%r", key, exc)
        return None
    base = settings.minio_public_url.rstrip("/")
    return f"{base}/{bucket}/{key}"


def _replace_tag(content: str, tag: ImgTag, url: str) -> str:
    """Substitute ``tag.full_match`` with ``<img src="<url>" {attrs} />``.

    Keeps every original attr except ``data-omnia-gen`` (which has been
    consumed). Collapses runs of whitespace inside the tag for tidiness.
    """
    attrs = f"{tag.pre_attrs} {tag.post_attrs}".strip()
    attrs = _GROUP_RE.sub("", attrs).strip()  # drop the consumed group marker
    if attrs:
        replacement = f'<img src="{url}" {attrs} />'
    else:
        replacement = f'<img src="{url}" />'
    replacement = re.sub(r"\s{2,}", " ", replacement)
    return content.replace(tag.full_match, replacement, 1)


_UNRESOLVED_IMG_RE = re.compile(
    r"<img\b[^>]*\bdata-omnia-(?:gen|photo)\b[^>]*>", re.IGNORECASE
)
_CLASS_ATTR_RE = re.compile(r'\bclass\s*=\s*"([^"]*)"', re.IGNORECASE)
_STYLE_ATTR_RE = re.compile(r'\bstyle\s*=\s*"([^"]*)"', re.IGNORECASE)
# Same-size fallback fill so a dropped photo slot keeps its shape + a kit-toned
# gradient instead of collapsing into an empty hole (the "съехавший дизайн"
# symptom when a content photo 403s). Picks up the project palette vars.
_PLACEHOLDER_FILL = (
    "background:linear-gradient(135deg,var(--brand,#1e293b),var(--accent,#0ea5e9));"
)


def _img_to_placeholder(match: re.Match[str]) -> str:
    """Turn an unresolved data-omnia <img> into a same-sized gradient <div> so the
    section keeps its layout (height/rounding from the original classes) with a
    kit-toned fill — never an empty collapsed block."""
    tag = match.group(0)
    cls_m = _CLASS_ATTR_RE.search(tag)
    sty_m = _STYLE_ATTR_RE.search(tag)
    cls_attr = f' class="{cls_m.group(1)}"' if cls_m else ""
    style_val = sty_m.group(1).strip() if sty_m else ""
    low = style_val.lower()
    if "gradient" not in low and "background" not in low:
        style_val = (style_val + ";" if style_val else "") + _PLACEHOLDER_FILL
    return f'<div{cls_attr} style="{style_val}" aria-hidden="true"></div>'


def strip_unresolved_tags(files: dict[str, str]) -> tuple[dict[str, str], int]:
    """Replace any ``<img data-omnia-gen|photo …>`` still unresolved after
    ``resolve_images`` with a same-sized gradient placeholder ``<div>`` — keeps
    the section's shape + a kit-toned fill instead of a broken/empty box (the
    cause of the "съехавший" layout when a CDN 403s a content photo). Returns
    ``(files, replaced_count)``. No-op when every image already resolved."""
    out: dict[str, str] = {}
    replaced = 0
    for path, content in files.items():
        if path.lower().endswith((".html", ".htm")):
            new, n = _UNRESOLVED_IMG_RE.subn(_img_to_placeholder, content)
            replaced += n
            out[path] = new
        else:
            out[path] = content
    return out, replaced


async def resolve_images(
    files: dict[str, str],
    project_id: str,
    on_image: Callable[[int, str], Awaitable[None]] | None = None,
) -> tuple[dict[str, str], int, int]:
    """Resolve every ``data-omnia-gen`` tag in ``files`` to a real image URL.

    Returns ``(new_files, resolved_count, total_tags)``. Files without tags
    pass through untouched. Tags whose gateway/MinIO call failed are left in
    place so subsequent passes (or the next prompt) can retry.

    ``on_image(preview_idx, url)`` — optional async callback fired the moment a
    generated image is ready, for each tag sharing that prompt in index.html.
    ``preview_idx`` is the tag's position among index.html's data-omnia-gen imgs
    (document order), so the streaming preview can drop the photo into the right
    frame live. Best-effort: callback errors never abort resolution.
    """
    tags = extract_image_tags(files)
    photo_tags = _extract_photo_tags(files)
    if not tags and not photo_tags:
        return files, 0, 0

    settings = get_settings()
    sem = asyncio.Semaphore(_CONCURRENCY)

    # Group dedup (cost control): tags sharing a data-omnia-gen-group collapse to
    # ONE generation — the group's FIRST prompt — and every member reuses that
    # image. Product / menu / gallery cards thus cost ONE image, not N; concept
    # and hero tags (no group) still generate uniquely. `_eff` maps a tag to the
    # prompt actually generated for it.
    _group_rep: dict[str, str] = {}
    for _t in tags:
        if _t.group and _t.group not in _group_rep:
            _group_rep[_t.group] = _t.prompt

    def _eff(t: ImgTag) -> str:
        return _group_rep[t.group] if (t.group and t.group in _group_rep) else t.prompt

    # Unique effective prompts in document order. Generation is capped at the
    # per-page budget; effective prompts BEYOND it are not generated — their tags
    # reuse an already-made image below, so cost is bounded and nothing ships
    # broken.
    _seen: set[str] = set()
    unique_eff: list[str] = []
    for _t in tags:
        _e = _eff(_t)
        if _e not in _seen:
            _seen.add(_e)
            unique_eff.append(_e)
    _budget = max(1, int(settings.image_gen_max_unique))
    gen_prompts = unique_eff[:_budget]
    overflow_eff = set(unique_eff[_budget:])

    # Live drop-in mapping: for each effective prompt, the document-order indices
    # of its tags AMONG index.html's data-omnia-gen imgs — matches the streaming
    # preview's querySelectorAll('img[data-omnia-gen]') order, so the frontend
    # swaps the correct frame the instant each image resolves. Grouped tags all
    # receive the shared image; tags in other files (no live preview) are skipped.
    _preview_idx: dict[int, int] = {}
    _i = 0
    for _t in tags:
        if _t.file_path == "index.html":
            _preview_idx[id(_t)] = _i
            _i += 1
    _eff_to_preview: dict[str, list[int]] = {}
    for _t in tags:
        _pi = _preview_idx.get(id(_t))
        if _pi is not None:
            _eff_to_preview.setdefault(_eff(_t), []).append(_pi)

    async def _emit(orig_prompt: str, url: str) -> None:
        if on_image is None:
            return
        for _pi in _eff_to_preview.get(orig_prompt, []):
            try:
                await on_image(_pi, url)
            except Exception:
                pass

    # AI image generation is GATED and TIME-BOXED. When the gpt-image upstream is
    # down (proxyapi dry / OpenAI unreachable from the RU egress) each _fetch_one
    # burns its full 75s timeout; N tags × that stalls the whole build for
    # minutes before any snapshot — the preview "loads forever". The kill switch
    # skips generation outright; the deadline bounds it even when enabled.
    # Unresolved tags are stripped below, so the section's CSS/mesh fallback
    # shows instead of a broken <img> (R-10: explicit timeout, degrade fast).
    prompt_to_url: dict[str, str] = {}
    if tags and settings.use_image_gen:
        # Phase N+ — enrich short/weak prompts via the `image_prompt` role before
        # generation. Detailed prompts (>= _ENRICH_MIN_WORDS words) pass through
        # untouched; the kill switch turns the whole step into identity.
        if settings.use_image_prompt_enrichment:

            async def _maybe_enrich(p: str) -> tuple[str, str]:
                if len(p.split()) >= _ENRICH_MIN_WORDS:
                    return p, p
                async with sem:
                    return p, await _enrich_prompt(p)

            enrich_pairs = await asyncio.gather(
                *[_maybe_enrich(p) for p in gen_prompts]
            )
            gen_prompt_for = {orig: gen for orig, gen in enrich_pairs}
        else:
            gen_prompt_for = {p: p for p in gen_prompts}

        async def _resolve(orig_prompt: str) -> tuple[str, str | None]:
            # Generate + content-address by the (possibly enriched) gen prompt,
            # but key the result by the ORIGINAL prompt so tag replacement matches.
            gen_prompt = gen_prompt_for.get(orig_prompt, orig_prompt)
            # Cost control: reuse an already-generated image for this exact
            # (project, prompt) instead of paying for an identical gen again.
            # Kills the repair-reroll / re-generation doubling.
            cached = await asyncio.to_thread(_cached_image_url, project_id, gen_prompt)
            if cached:
                return orig_prompt, cached
            async with sem:
                img = await _fetch_one(gen_prompt)
                if img is None:
                    return orig_prompt, None
                url = await asyncio.to_thread(_upload_image, img, project_id, gen_prompt)
                return orig_prompt, url

        # Resolve via as_completed so each image fires its `on_image` event the
        # moment it's ready (the live drop-in), not in one batch at the end. The
        # semaphore keeps generation serial, so events arrive naturally spaced.
        # The overall budget still bounds the phase: on timeout we keep whatever
        # resolved and cancel stragglers so the build always ships (R-10).
        _tasks = [asyncio.ensure_future(_resolve(p)) for p in gen_prompts]
        pairs: list[tuple[str, str | None]] = []
        try:
            for _fut in asyncio.as_completed(
                _tasks, timeout=_IMAGE_RESOLVE_BUDGET_S
            ):
                orig_prompt, url = await _fut
                pairs.append((orig_prompt, url))
                if url:
                    await _emit(orig_prompt, url)
        except TimeoutError:
            log.warning(
                "image_resolver: gen budget %.0fs exceeded (%d prompts) — "
                "stripping unresolved tags so the build ships",
                _IMAGE_RESOLVE_BUDGET_S,
                len(gen_prompts),
            )
        finally:
            for _task in _tasks:
                if not _task.done():
                    _task.cancel()
        prompt_to_url = {p: u for p, u in pairs if u}

    # Any successfully generated image — reused for over-budget card tags so the
    # page never ships a broken <img> and never spends on extra generations.
    reuse_url = next(iter(prompt_to_url.values()), None)
    new_files = dict(files)
    resolved = 0
    for tag in tags:
        eff = _eff(tag)
        url = prompt_to_url.get(eff)
        if url is None and eff in overflow_eff:
            url = reuse_url  # over-budget → reuse a generated image, no new spend
        if not url:
            # Attempted-but-failed gen (gateway 502 / timeout), or nothing
            # generated at all. STRIP the unresolved tag so a broken <img> (raw
            # alt-text in an empty box) never ships — mirrors the photo path
            # below. The section's background/layout shows instead. (Owner
            # screenshot 2026-06-02: a failed hero gen rendered "alt" in a box.)
            new_files[tag.file_path] = new_files[tag.file_path].replace(
                tag.full_match, "", 1
            )
            continue
        new_files[tag.file_path] = _replace_tag(
            new_files[tag.file_path], tag, url
        )
        resolved += 1

    # ── Pexels stock photos ──────────────────────────────────────────────
    # Disabled / no key / fetch fail → STRIP the tag so the section's flat
    # or mesh fallback shows, never a broken <img> (the bg layer sits behind).
    if photo_tags:
        photo_on = settings.photo_source == "openverse" or (
            settings.photo_source == "pexels" and settings.pexels_api_key is not None
        )
        kw_to_url: dict[str, str] = {}
        if photo_on:
            unique_kw = list({t.prompt for t in photo_tags})

            async def _resolve_photo(kw: str) -> tuple[str, str | None]:
                async with sem:
                    img = await _fetch_stock_photo(kw, project_id)
                if img is None:
                    return kw, None
                url = await asyncio.to_thread(_upload_photo, img, project_id, kw)
                return kw, url

            ph_pairs = await asyncio.gather(*[_resolve_photo(k) for k in unique_kw])
            kw_to_url = {k: u for k, u in ph_pairs if u}
        for tag in photo_tags:
            url = kw_to_url.get(tag.prompt)
            if url:
                new_files[tag.file_path] = _replace_tag(
                    new_files[tag.file_path], tag, url
                )
                resolved += 1
            else:
                new_files[tag.file_path] = new_files[tag.file_path].replace(
                    tag.full_match, "", 1
                )

    return new_files, resolved, len(tags) + len(photo_tags)
