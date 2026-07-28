#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from PIL import Image, ImageDraw
from playwright.async_api import BrowserContext, Page, async_playwright


def _make_sample_image(path: Path) -> None:
    img = Image.new("RGB", (960, 720), "#1a1a24")
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((120, 120, 840, 600), radius=64, fill="#6f5ce7")
    draw.rectangle((220, 220, 740, 500), fill="#f4efe7")
    draw.text((250, 280), "HERO MEDIA", fill="#111111")
    draw.text((250, 360), "E2E SAMPLE", fill="#222222")
    img.save(path, format="PNG")


async def _wait_until(
    fn,
    *,
    timeout_s: float = 90,
    interval_s: float = 1.0,
    label: str,
):
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        last = await fn()
        if last:
            return last
        await asyncio.sleep(interval_s)
    raise TimeoutError(f"Timed out waiting for {label}")


async def _register(page: Page, email: str, password: str, web_url: str) -> None:
    await page.goto(f"{web_url}/register", wait_until="domcontentloaded")
    await page.fill("#email", email)
    await page.fill("#password", password)
    await page.fill("#confirm", password)
    await page.get_by_role("button", name="Создать аккаунт").click()
    await page.wait_for_url("**/projects", timeout=45_000)


async def _browser_fetch_json(page: Page, api_url: str, path: str, payload: dict[str, Any]) -> Any:
    return await page.evaluate(
        """async ({ apiUrl, path, payload }) => {
          const res = await fetch(`${apiUrl}${path}`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          });
          const text = await res.text();
          if (!res.ok) {
            throw new Error(text || `HTTP ${res.status}`);
          }
          return JSON.parse(text);
        }""",
        {"apiUrl": api_url, "path": path, "payload": payload},
    )


async def _http_client_from_context(context: BrowserContext, api_url: str) -> httpx.AsyncClient:
    cookies = await context.cookies(api_url)
    jar = {cookie["name"]: cookie["value"] for cookie in cookies}
    return httpx.AsyncClient(base_url=api_url, cookies=jar, follow_redirects=True, timeout=30.0)


async def run_ui_flow(
    web_url: str,
    api_url: str,
    artifacts_dir: Path,
    *,
    prompt: str,
    business_type: str,
    style_preference: str,
    focus_preference: str,
    motion_preference: str,
    override_plan: str,
    scenario_label: str,
    stub_mode: bool,
    retry_render: bool,
    render_timeout_s: float,
) -> dict[str, Any]:
    email = f"hero-media-e2e-{int(time.time())}-{uuid4().hex[:6]}@example.com"
    password = "HeroMedia123"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        await _register(page, email, password, web_url)
        project = await _browser_fetch_json(
          page,
          api_url,
          "/api/projects",
          {"name": f"Hero Media E2E {uuid4().hex[:6]}", "template": "landing"},
        )
        project_id = project["id"]
        slug = project["slug"]

        await page.goto(f"{web_url}/projects/{project_id}", wait_until="domcontentloaded")
        await page.get_by_test_id("hero-media-toggle").wait_for(state="visible")
        await page.wait_for_timeout(1500)
        await page.get_by_test_id("hero-media-toggle").click()
        try:
            await page.get_by_test_id("hero-media-panel").wait_for(state="visible", timeout=5000)
        except Exception:
            # Hydration race on the first workspace paint: the toolbar button can
            # be in the DOM before React wires its onClick. One retry after a
            # short beat proves whether it's a real UI bug or just early input.
            await page.wait_for_timeout(1200)
            await page.get_by_test_id("hero-media-toggle").click()
            try:
                await page.get_by_test_id("hero-media-panel").wait_for(state="visible", timeout=5000)
            except Exception:
                await page.screenshot(
                    path=str(artifacts_dir / f"{scenario_label}-panel-open-failed.png")
                )
                (artifacts_dir / f"{scenario_label}-panel-open-failed.html").write_text(
                    await page.content(),
                    encoding="utf-8",
                )
                raise
        await page.get_by_test_id("hero-media-consent").check()

        with tempfile.TemporaryDirectory(prefix="hero-media-e2e-") as tmp:
            sample = Path(tmp) / "sample.png"
            _make_sample_image(sample)
            await page.get_by_test_id("hero-media-upload-input").set_input_files(str(sample))

        await _wait_until(
            lambda: page.locator("[data-testid='hero-media-panel'] img").count(),
            label="uploaded asset thumbnail",
        )
        await page.get_by_test_id("hero-media-prompt").fill(prompt)
        await page.get_by_test_id("hero-media-business-type").fill(business_type)
        await page.get_by_test_id("hero-media-style").fill(style_preference)
        await page.get_by_test_id(f"hero-media-focus-{focus_preference}").click()
        await page.get_by_test_id(f"hero-media-motion-{motion_preference}").click()
        await page.get_by_test_id("hero-media-build-plan").click()

        await page.get_by_test_id("hero-media-approve-plan").wait_for(timeout=45_000)
        await page.get_by_test_id(f"hero-media-plan-option-{override_plan}").click()
        await page.get_by_test_id("hero-media-approve-plan").click()

        await page.get_by_test_id("hero-media-render").click()
        await page.get_by_test_id("hero-media-preview-frame").wait_for(timeout=60_000)
        await page.screenshot(path=str(artifacts_dir / f"{scenario_label}-panel.png"))

        client = await _http_client_from_context(context, api_url)
        try:
            first_render = await _wait_until(
                lambda: _poll_render(client, project_id),
                timeout_s=render_timeout_s,
                label="first render completion",
            )
            snapshots_before = await _snapshots(client, project_id)
            second_render = None
            if retry_render:
                await page.get_by_test_id("hero-media-retry-render").click()
                second_render = await _wait_until(
                    lambda: _poll_render(
                        client,
                        project_id,
                        at_least=len(first_render["progress_log"]) + 1,
                    ),
                    timeout_s=render_timeout_s,
                    label="retry render completion",
                )

            try:
                await page.get_by_test_id("hero-media-apply").click()
            except Exception:
                await page.screenshot(
                    path=str(artifacts_dir / f"{scenario_label}-apply-failed.png")
                )
                (artifacts_dir / f"{scenario_label}-apply-failed.html").write_text(
                    await page.content(),
                    encoding="utf-8",
                )
                raise
            new_snapshot = await _wait_until(
                lambda: _poll_new_snapshot(client, project_id, len(snapshots_before)),
                timeout_s=90,
                label="hero-media snapshot apply",
            )
            public_preview = await client.get(f"/p/{slug}")
            public_preview.raise_for_status()
        finally:
            await client.aclose()
            await browser.close()

    return {
        "mode": "ui-flow",
        "scenario": scenario_label,
        "project_id": project_id,
        "slug": slug,
        "first_render_id": first_render["id"],
        "second_render_id": second_render["id"] if second_render else None,
        "applied_snapshot_id": new_snapshot["id"],
        "recommended_plan": first_render["media_plan"],
        "provider_summary": first_render.get("provider_summary"),
        "public_preview_contains_marker": "OMNIA_HERO_MEDIA_START" in public_preview.text,
        "stub_mode": stub_mode,
        "retried": retry_render,
    }


async def _poll_render(
    client: httpx.AsyncClient,
    project_id: str,
    *,
    at_least: int | None = None,
) -> dict[str, Any] | None:
    resp = await client.get(f"/api/projects/{project_id}/hero-media/renders")
    resp.raise_for_status()
    renders = resp.json()
    if not renders:
        return None
    render = renders[0]
    if at_least is not None and len(render.get("progress_log") or []) < at_least:
        return None
    return render if render["status"] == "completed" else None


async def _snapshots(client: httpx.AsyncClient, project_id: str) -> list[dict[str, Any]]:
    resp = await client.get(f"/api/projects/{project_id}/snapshots")
    resp.raise_for_status()
    return resp.json()


async def _poll_new_snapshot(
    client: httpx.AsyncClient,
    project_id: str,
    count_before: int,
) -> dict[str, Any] | None:
    snapshots = await _snapshots(client, project_id)
    if len(snapshots) <= count_before:
        return None
    latest = snapshots[0]
    if latest.get("preview_url"):
        return latest
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--web-url", default="http://127.0.0.1:3000")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--scenario-label", default="hero-media")
    parser.add_argument(
        "--prompt",
        default="Покажите товар крупно, свет на материале и ощущение дорогого вечернего бренда.",
    )
    parser.add_argument("--business-type", default="fashion product")
    parser.add_argument("--style-preference", default="premium editorial")
    parser.add_argument("--focus-preference", default="product")
    parser.add_argument("--motion-preference", default="cinematic")
    parser.add_argument("--override-plan", default="motion")
    parser.add_argument("--stub-mode", action="store_true")
    parser.add_argument("--skip-retry", action="store_true")
    parser.add_argument("--render-timeout-s", type=float, default=60.0)
    parser.add_argument(
        "--artifacts-dir",
        default=str(Path("artifacts") / "hero-media-e2e"),
    )
    args = parser.parse_args()
    result = asyncio.run(
        run_ui_flow(
            args.web_url,
            args.api_url,
            Path(args.artifacts_dir),
            prompt=args.prompt,
            business_type=args.business_type,
            style_preference=args.style_preference,
            focus_preference=args.focus_preference,
            motion_preference=args.motion_preference,
            override_plan=args.override_plan,
            scenario_label=args.scenario_label,
            stub_mode=args.stub_mode,
            retry_render=not args.skip_retry,
            render_timeout_s=args.render_timeout_s,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
