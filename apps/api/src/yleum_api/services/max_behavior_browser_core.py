"""Reviewed painted A/C browser measurements, called only by the private controller driver."""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, cast
from uuid import uuid4


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class Failure(Exception):
    def __init__(self, code: str, status: str = "NEEDS_CHANGES") -> None:
        super().__init__(code)
        self.code = code
        self.status = status


def need(value: object, code: str, status: str = "NEEDS_CHANGES") -> None:
    if not value:
        raise Failure(code, status)


def visible(page: Any, selector: str) -> Any:
    locator = page.locator(selector)
    need(locator.count() == 1, "required_control_missing")
    painted(page, locator)
    return locator


PAINT = """e=>{
  const s=getComputedStyle(e),r=e.getBoundingClientRect();
  let opacity=1,hiddenDisplay=false,hiddenContent=false,hiddenVisibility=false;
  let unsupportedPaint=false,depth=0;
  for(let p=e;p;p=p.parentElement){
    if(++depth>64)break;
    const x=getComputedStyle(p);opacity*=Number(x.opacity);
    hiddenDisplay ||= x.display==='none';hiddenContent ||= x.contentVisibility==='hidden';
    hiddenVisibility ||= x.visibility==='hidden'||x.visibility==='collapse';
    unsupportedPaint ||= x.filter!=='none'||x.backdropFilter!=='none'||x.clipPath!=='none'
      ||x.maskImage!=='none'||x.clip!=='auto'||x.mixBlendMode!=='normal';
  }
  const control=e.matches('button,select,input,[role="button"]');
  const cx=r.x+r.width/2,cy=r.y+r.height/2;
  const top=document.elementFromPoint(cx,cy);
  return {tag:e.tagName,opacity:Number(s.opacity),effective_opacity:opacity,
    visibility:s.visibility,display:s.display,ancestor_hidden_display:hiddenDisplay,
    ancestor_hidden_content:hiddenContent,ancestor_hidden_visibility:hiddenVisibility,
    unsupported_paint:unsupportedPaint,
    ancestor_budget:depth<=64,
    css_visible:e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true,contentVisibilityAuto:true}),
    box:{x:r.x,y:r.y,width:r.width,height:r.height},control,
    in_view:r.width>0&&r.height>0&&r.right>0&&r.bottom>0&&r.x<innerWidth&&r.y<innerHeight,
    control_in_view:r.x>=-.5&&r.y>=-.5&&r.right<=innerWidth+.5&&r.bottom<=innerHeight+.5,
    hit:!!top&&(top===e||e.contains(top))};
}"""


def painted(page: Any, locator: Any) -> dict[str, Any]:
    """A CSS/layout witness, with normal scrolling before viewport checks.

    Ancestor visibility is recorded; a child may legitimately override inherited
    visibility:hidden. checkVisibility evaluates its actual resolved visibility.
    Ancestor opacity and display/content suppression cannot be overridden.
    """
    need(time.monotonic() < page._behavior_deadline, "browser_budget", "NEEDS_REVIEW")
    initial = locator.evaluate(PAINT)
    checks = getattr(page, "_qa_paint_witnesses", [])
    checks.append(initial)
    need(initial["ancestor_budget"], "paint_ancestor_budget")
    need(
        initial["css_visible"]
        and initial["effective_opacity"] > 0
        and not initial["ancestor_hidden_display"]
        and not initial["ancestor_hidden_content"]
        and initial["visibility"] == "visible",
        "required_surface_unpainted",
    )
    need(not initial["unsupported_paint"], "paint_requires_review", "NEEDS_REVIEW")
    try:
        locator.scroll_into_view_if_needed(timeout=2000)
    except Exception:
        raise Failure("paint_scroll_unavailable") from None
    result = locator.evaluate(PAINT)
    checks.append(result)
    need(result["css_visible"] and result["effective_opacity"] > 0, "required_surface_unpainted")
    need(not result["unsupported_paint"], "paint_requires_review", "NEEDS_REVIEW")
    need(result["in_view"], "paint_bounds_outside_viewport")
    if result["control"]:
        need(result["control_in_view"], "control_bounds_outside_viewport")
        need(result["hit"], "required_control_occluded")
    return cast(dict[str, Any], result)


def click(page: Any, selector: str) -> None:
    locator = visible(page, selector)
    need(locator.is_enabled(), "required_control_disabled")
    box = locator.bounding_box()
    need(box and box["width"] >= 44 and box["height"] >= 44, "control_below_44px")
    locator.click(timeout=2000)


def named_button(page: Any, selector: str, name: str) -> None:
    locator = visible(page, selector)
    named = page.get_by_role("button", name=name, exact=True)
    need(named.count() == 1 and named.is_visible(), "required_control_name_missing")
    need(locator.evaluate("(e,n)=>e===n", named.element_handle()), "required_control_name_missing")


def storage(page: Any, key: str, expected: str) -> None:
    actual = page.evaluate("(key)=>localStorage.getItem(key)", key)
    need(actual == expected, "local_preference_missing")


def density_measure(page: Any, view: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    cards = page.locator(view["cards"])
    need(cards.count() >= 2 and cards.count() <= 100, "density_cards_missing")
    for mode in ("normal", "compact"):
        named_button(page, cfg[mode], cfg["names"][mode])
    for control in page.locator(cfg["controls"]).all():
        if control.is_visible():
            painted(page, control)
            box = control.bounding_box()
            need(box and box["width"] >= 44 and box["height"] >= 44, "control_below_44px")
    # Sample both ends by ordinary scrolling, then return to the first card.
    # Offscreen list rows are valid; never require the whole list in the viewport.
    painted(page, cards.first)
    painted(page, cards.last)
    painted(page, cards.first)
    # Values exist only in process memory; export a canonical digest, never text.
    values = cards.evaluate_all(
        "els=>els.map(e=>({id:e.getAttribute('data-item-id'),text:e.textContent}))"
    )
    need(all(x["id"] for x in values), "stable_item_identity_missing")
    dims = cards.evaluate_all(
        "els=>els.map(e=>{const s=getComputedStyle(e),b=e.getBoundingClientRect();"
        "return {padding:parseFloat(s.paddingTop),height:b.height,width:b.width,x:b.x,y:b.y}})"
    )
    gap = page.locator(view["container"]).evaluate("e=>parseFloat(getComputedStyle(e).rowGap)")
    need(all(x["width"] > 0 and x["height"] > 0 for x in dims), "card_geometry")
    need(
        all(
            x["x"] >= -0.5 and x["x"] + x["width"] <= page.viewport_size["width"] + 0.5
            for x in dims
        ),
        "card_clipping",
    )
    filters = page.locator(cfg["filter"]).input_value()
    need(filters == cfg["filter_value"], "filter_changed")
    return {"data_sha256": digest(values), "count": len(values), "cards": dims, "gap": gap}


def check_density(page: Any, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    for mode in ("normal", "compact"):
        named_button(page, cfg[mode], cfg["names"][mode])
    result = []
    for view in cfg["views"]:
        click(page, view["control"])
        visible(page, cfg["filter"]).select_option("all")
        all_before = page.locator(view["cards"]).evaluate_all(
            "els=>els.map(e=>({id:e.getAttribute('data-item-id'),text:e.textContent}))"
        )
        visible(page, cfg["filter"]).select_option(cfg["filter_value"])
        click(page, cfg["normal"])
        normal = density_measure(page, view, cfg)
        click(page, cfg["compact"])
        compact = density_measure(page, view, cfg)
        need(normal["data_sha256"] == compact["data_sha256"], "density_data_changed")
        need(
            compact["gap"] < normal["gap"]
            and all(
                c["padding"] < n["padding"] and c["height"] < n["height"]
                for n, c in zip(normal["cards"], compact["cards"], strict=True)
            ),
            "density_geometry_unchanged",
        )
        storage(page, cfg["storage_key"], "compact")
        page.reload(wait_until="networkidle", timeout=5000)
        storage(page, cfg["storage_key"], "compact")
        click(page, view["control"])
        visible(page, cfg["filter"]).select_option(cfg["filter_value"])
        reloaded = density_measure(page, view, cfg)
        need(
            reloaded["gap"] == compact["gap"]
            and reloaded["cards"] == compact["cards"]
            and reloaded["data_sha256"] == compact["data_sha256"],
            "density_reload_changed",
        )
        click(page, cfg["normal"])
        restored = density_measure(page, view, cfg)
        need(restored == normal, "density_not_reversible")
        visible(page, cfg["filter"]).select_option("all")
        all_after = page.locator(view["cards"]).evaluate_all(
            "els=>els.map(e=>({id:e.getAttribute('data-item-id'),text:e.textContent}))"
        )
        need(digest(all_before) == digest(all_after), "all_visible_data_changed")
        result.append(
            {
                "normal": normal,
                "compact": compact,
                "reload": reloaded,
                "all_items_sha256": digest(all_after),
            }
        )
    return result


def theme_measure(page: Any, cfg: dict[str, Any]) -> dict[str, str]:
    values = {}
    for name, selector, prop in (
        ("body", "body", "backgroundColor"),
        ("card", cfg["card"], "backgroundColor"),
        ("text", cfg["text"], "color"),
    ):
        loc = page.locator(selector).first
        need(loc.count() == 1 and loc.is_visible(), "theme_surface_missing")
        painted(page, loc)
        paint = loc.evaluate(
            "e=>{const s=getComputedStyle(e);let opaque=true;for(let p=e;p;p=p.parentElement){"
            "if(getComputedStyle(p).opacity!=='1')opaque=false;}"
            "return {image:s.backgroundImage,opaque}}"
        )
        need(
            paint["image"] == "none" and paint["opaque"],
            "theme_paint_requires_review",
            "NEEDS_REVIEW",
        )
        values[name] = loc.evaluate("(e,p)=>getComputedStyle(e)[p]", prop)
    return values


def luminance(color: str) -> float:
    match = re.fullmatch(r"rgb\((\d+), (\d+), (\d+)\)", color)
    need(match is not None, "theme_paint_requires_review", "NEEDS_REVIEW")
    assert match is not None
    channels = [int(x) / 255 for x in match.groups()]
    channels = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in channels]
    return sum(x * y for x, y in zip(channels, (0.2126, 0.7152, 0.0722), strict=True))


def check_theme(page: Any, cfg: dict[str, Any]) -> dict[str, dict[str, str]]:
    if cfg.get("view_control"):
        click(page, cfg["view_control"])
    # Controls must actually be inside the visible header, not elsewhere in DOM.
    header = visible(page, cfg["header"])
    for selector in (cfg["light"], cfg["dark"]):
        control = visible(page, selector)
        need(
            control.evaluate("(e,s)=>!!e.closest(s)", cfg["header"]), "theme_control_outside_header"
        )
    for mode in ("light", "dark"):
        named_button(page, cfg[mode], cfg["names"][mode])
    need(header.is_visible(), "theme_header_hidden")
    click(page, cfg["light"])
    light = theme_measure(page, cfg)
    click(page, cfg["dark"])
    dark = theme_measure(page, cfg)
    need(all(light[key] != dark[key] for key in light), "theme_computed_style_unchanged")
    for surface in ("body", "card"):
        need(
            luminance(light[surface]) >= 0.75 and luminance(dark[surface]) <= 0.18,
            "theme_light_dark_luminance",
        )
    for colors in (light, dark):
        background, text = luminance(colors["card"]), luminance(colors["text"])
        need(
            (max(background, text) + 0.05) / (min(background, text) + 0.05) >= 4.5,
            "theme_text_contrast",
        )
    storage(page, cfg["storage_key"], "dark")
    page.reload(wait_until="networkidle", timeout=5000)
    storage(page, cfg["storage_key"], "dark")
    reloaded = theme_measure(page, cfg)
    need(reloaded == dark, "theme_reload_changed")
    click(page, cfg["light"])
    need(theme_measure(page, cfg) == light, "theme_not_reversible")
    return {"light": light, "dark": dark, "reload": reloaded}


def check_coffee_summary(page: Any, cfg: dict[str, str]) -> list[dict[str, Any]]:
    """Exact authorized button action; no assumptions about summary field labels/order."""
    named_button(page, cfg["button"], "Проверить заявку")
    form = page.locator(cfg["form"])
    field = visible(page, cfg["text_input"])
    summary = page.locator(cfg["summary"])
    need(form.count() == 1 and summary.count() == 1, "coffee_adapter_missing", "NEEDS_REVIEW")
    need(
        field.evaluate("e=>e.tagName==='TEXTAREA'||(e.tagName==='INPUT'&&e.type==='text')"),
        "coffee_input_unsupported",
        "NEEDS_REVIEW",
    )
    need(
        field.evaluate("(e,s)=>e.closest(s)!==null", cfg["form"]),
        "coffee_input_outside_form",
        "NEEDS_REVIEW",
    )
    state_script = (
        "e=>Array.from(e.querySelectorAll('input,select,textarea')).map(x=>({"
        "tag:x.tagName,name:x.name,type:x.type,value:x.value,checked:x.checked}))"
    )
    result = []
    initial = field.input_value()
    try:
        for _ in range(2):
            value = "QA" + uuid4().hex[:12]
            field.fill(value)
            need(field.input_value() == value, "coffee_input_constraint", "NEEDS_REVIEW")
            before_form = digest(form.evaluate(state_script))
            before_summary = digest(summary.text_content())
            click(page, cfg["button"])
            painted(page, summary)
            after = summary.text_content() or ""
            after_form = digest(form.evaluate(state_script))
            need(
                value in after and digest(after) != before_summary,
                "coffee_summary_not_input_driven",
            )
            need(before_form == after_form, "coffee_form_state_changed")
            value_sha = hashlib.sha256(value.encode()).hexdigest()
            result.append(
                {
                    "input_sha256": value_sha,
                    "echoed_input_sha256": value_sha,
                    "summary_before_sha256": before_summary,
                    "summary_after_sha256": digest(after),
                    "form_before_sha256": before_form,
                    "form_after_sha256": after_form,
                    # The driver enforces these network properties before accepting.
                    "business_writes": 0,
                    "provider_requests": 0,
                }
            )
    finally:
        field.fill(initial)
    return result
