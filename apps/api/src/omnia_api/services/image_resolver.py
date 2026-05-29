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
    "You are an image-prompt engineer for a photo generator. Expand the short "
    "description into ONE detailed English prompt covering subject, scene, "
    "style, lighting, angle and lens. Return ONLY the prompt text — no quotes, "
    "no preamble, no commentary."
)

# Hard ceiling per generation. Beyond this we log a warning and leave extra
# tags untouched (they render as broken images — visible signal to the user
# without burning the wallet).
MAX_IMAGES_PER_RESOLVE = 30

# Concurrent gateway calls. proxyapi tolerates moderate parallelism; pick a
# conservative ceiling so one fat prompt doesn't saturate the gateway.
_CONCURRENCY = 4

# Per-image timeout (sec) — slightly over the gateway's internal 60s ceiling
# so client-side aborts come from us, not from the upstream.
_REQUEST_TIMEOUT = 75.0

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
        "model": "gpt-image-1",
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
    if not tags:
        return files, 0, 0

    settings = get_settings()
    unique_prompts = list({t.prompt for t in tags})
    sem = asyncio.Semaphore(_CONCURRENCY)

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
        # Generate + content-address by the (possibly enriched) gen prompt, but
        # key the result by the ORIGINAL prompt so tag replacement still matches.
        gen_prompt = gen_prompt_for.get(orig_prompt, orig_prompt)
        async with sem:
            img = await _fetch_one(gen_prompt)
            if img is None:
                return orig_prompt, None
            url = await asyncio.to_thread(_upload_image, img, project_id, gen_prompt)
            return orig_prompt, url

    pairs = await asyncio.gather(*[_resolve(p) for p in unique_prompts])
    prompt_to_url = {p: u for p, u in pairs if u}

    new_files = dict(files)
    resolved = 0
    for tag in tags:
        url = prompt_to_url.get(tag.prompt)
        if not url:
            continue
        new_files[tag.file_path] = _replace_tag(
            new_files[tag.file_path], tag, url
        )
        resolved += 1

    return new_files, resolved, len(tags)
