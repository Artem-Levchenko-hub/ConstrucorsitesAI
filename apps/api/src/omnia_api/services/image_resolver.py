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

# Pexels search endpoint. Landscape orientation suits full-bleed section
# backgrounds; per_page gives a small deterministic pick-pool per keyword.
_PEXELS_SEARCH_URL = "https://api.pexels.com/v1/search"
_PEXELS_PER_PAGE = 15


@dataclass(frozen=True)
class ImgTag:
    file_path: str
    full_match: str
    prompt: str
    pre_attrs: str
    post_attrs: str


def extract_image_tags(files: dict[str, str]) -> list[ImgTag]:
    """Find every ``data-omnia-gen`` tag across the given files.

    Caps the result at ``MAX_IMAGES_PER_RESOLVE`` — extras stay in the source
    untouched. Files outside ``_SCAN_EXTS`` are skipped (saves a regex run on
    css/json/md).
    """
    out: list[ImgTag] = []
    for path, content in files.items():
        if not path.lower().endswith(_SCAN_EXTS):
            continue
        for m in _TAG_RE.finditer(content):
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
                log.warning(
                    "image_resolver: tag cap %d hit (next tag in %s) — extras left as-is",
                    MAX_IMAGES_PER_RESOLVE,
                    path,
                )
                return out
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


def _upload_photo(image_bytes: bytes, project_id: str, keywords: str) -> str | None:
    """Cache a Pexels photo in MinIO, content-addressed by (project_id,
    keywords) so the same keyword in one project costs one fetch + one object."""
    settings = get_settings()
    client = get_minio_client()
    bucket = settings.minio_bucket_photos
    if not _ensure_bucket(client, bucket):
        return None
    sha = hashlib.sha256(
        f"pexels|{project_id}|{keywords}".encode("utf-8")
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
    if attrs:
        replacement = f'<img src="{url}" {attrs} />'
    else:
        replacement = f'<img src="{url}" />'
    replacement = re.sub(r"\s{2,}", " ", replacement)
    return content.replace(tag.full_match, replacement, 1)


async def resolve_images(
    files: dict[str, str], project_id: str
) -> tuple[dict[str, str], int, int]:
    """Resolve every ``data-omnia-gen`` tag in ``files`` to a real image URL.

    Returns ``(new_files, resolved_count, total_tags)``. Files without tags
    pass through untouched. Tags whose gateway/MinIO call failed are left in
    place so subsequent passes (or the next prompt) can retry.
    """
    tags = extract_image_tags(files)
    photo_tags = _extract_photo_tags(files)
    if not tags and not photo_tags:
        return files, 0, 0

    settings = get_settings()
    unique_prompts = list({t.prompt for t in tags})
    sem = asyncio.Semaphore(_CONCURRENCY)

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
                *[_maybe_enrich(p) for p in unique_prompts]
            )
            gen_prompt_for = {orig: gen for orig, gen in enrich_pairs}
        else:
            gen_prompt_for = {p: p for p in unique_prompts}

        async def _resolve(orig_prompt: str) -> tuple[str, str | None]:
            # Generate + content-address by the (possibly enriched) gen prompt,
            # but key the result by the ORIGINAL prompt so tag replacement matches.
            gen_prompt = gen_prompt_for.get(orig_prompt, orig_prompt)
            async with sem:
                img = await _fetch_one(gen_prompt)
                if img is None:
                    return orig_prompt, None
                url = await asyncio.to_thread(_upload_image, img, project_id, gen_prompt)
                return orig_prompt, url

        try:
            pairs = await asyncio.wait_for(
                asyncio.gather(*[_resolve(p) for p in unique_prompts]),
                timeout=_IMAGE_RESOLVE_BUDGET_S,
            )
        except (asyncio.TimeoutError, TimeoutError):
            log.warning(
                "image_resolver: gen budget %.0fs exceeded (%d prompts) — "
                "stripping unresolved tags so the build ships",
                _IMAGE_RESOLVE_BUDGET_S,
                len(unique_prompts),
            )
            pairs = []
        prompt_to_url = {p: u for p, u in pairs if u}

    new_files = dict(files)
    resolved = 0
    for tag in tags:
        url = prompt_to_url.get(tag.prompt)
        if not url:
            # Generation failed (gateway 502 / gpt-image timeout). STRIP the
            # unresolved tag so a broken <img> (raw alt-text in an empty box)
            # never ships — mirrors the photo path below. The section's
            # background/layout shows instead. (Owner screenshot 2026-06-02:
            # a failed hero gen rendered "alt" text inside a monochrome box.)
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
        photo_on = (
            settings.photo_source == "pexels" and settings.pexels_api_key is not None
        )
        kw_to_url: dict[str, str] = {}
        if photo_on:
            unique_kw = list({t.prompt for t in photo_tags})

            async def _resolve_photo(kw: str) -> tuple[str, str | None]:
                async with sem:
                    img = await _fetch_photo(kw, project_id)
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
