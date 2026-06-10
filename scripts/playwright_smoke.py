"""Streamlit UI の 5 画面 smoke (CLAUDE.md 「実 data smoke」 ルール)。

container 内で ``uv run python scripts/playwright_smoke.py`` 実行を想定。Streamlit
が起動済 (http://localhost:8000) であることが前提。

各 page で:
1. nav + Streamlit の "Running..." spinner が消えるまで待つ
2. screenshot を /tmp/bsllmner-viewer-smoke/ に保存
3. console / page error をログに集める
4. Sequence type sidebar widget の存在を確認

最後に: Home → Gap Discovery → Cohort と遷移し、Home で sequence_type='ChIP-Seq'
を選んだ後に Gap → Cohort で同じ tag が保持されているかを multiselect の DOM か
ら確認 (session_state navigation 不変条件 / SST-1+SST-4 の effective fix を
verify)。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

from playwright.async_api import (
    ConsoleMessage,
    Error,
    Page,
    Playwright,
    async_playwright,
)

BASE_URL = "http://localhost:8000"
PAGES: list[tuple[str, str]] = [
    ("home", BASE_URL),
    ("gap_discovery", f"{BASE_URL}/Gap_Discovery"),
    ("cohort", f"{BASE_URL}/Cohort"),
    ("gapminder", f"{BASE_URL}/Gapminder"),
    ("curation", f"{BASE_URL}/Curation"),
]

OUT_DIR = Path("/tmp/bsllmner-viewer-smoke")
OUT_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("playwright_smoke")


async def _wait_streamlit_idle(page: Page, *, timeout_ms: int = 60_000) -> None:
    """Wait until Streamlit finishes its initial run.

    Streamlit shows a status widget while running. The "RUNNING" attribute on
    <div data-testid="stStatusWidget"> disappears (becomes empty / removed)
    once the page has rendered. We poll for that, with a generous timeout for
    the cold-start aggregates load.
    """
    await page.wait_for_selector(
        '[data-testid="stApp"]', state="visible", timeout=timeout_ms
    )
    # The status widget exists for a short time then disappears or becomes
    # idle. Wait for either state.
    try:
        await page.wait_for_function(
            "() => {"
            "  const el = document.querySelector('[data-testid=\"stStatusWidget\"]');"
            "  if (!el) return true;"
            "  const txt = el.innerText || '';"
            "  return txt.trim() === '' || txt.includes('Source');"
            "}",
            timeout=timeout_ms,
        )
    except Exception:
        logger.warning("Streamlit status widget didn't quiesce in %d ms", timeout_ms)


async def _smoke_one(page: Page, label: str, url: str) -> dict[str, object]:
    errors: list[str] = []

    def _on_console(msg: ConsoleMessage) -> None:
        if msg.type in {"error"}:
            errors.append(f"console:{msg.type}:{msg.text}")

    def _on_pageerror(err: Error) -> None:
        errors.append(f"pageerror:{err.message}")

    page.on("console", _on_console)
    page.on("pageerror", _on_pageerror)
    logger.info("opening %s -> %s", label, url)
    await page.goto(url, wait_until="domcontentloaded")
    await _wait_streamlit_idle(page)
    # Give plots a moment to render so the screenshot is meaningful.
    await page.wait_for_timeout(2_500)
    screenshot = OUT_DIR / f"{label}.png"
    await page.screenshot(path=str(screenshot), full_page=True)
    has_seq_filter = await page.locator(
        'label:has-text("Sequence type")'
    ).count() > 0
    has_chip_radio = await page.locator(
        'label:has-text("ChIP-Atlas")'
    ).count() > 0
    return {
        "label": label,
        "url": url,
        "screenshot": str(screenshot),
        "errors": errors,
        "has_sequence_type_filter": has_seq_filter,
        "has_chip_atlas_radio": has_chip_radio,
    }


async def _verify_seq_type_persists(page: Page) -> dict[str, object]:
    """Set sequence_type=ChIP-Seq on Home, navigate to Gap Discovery + Cohort.

    Both downstream pages must surface "ChIP-Seq" inside the multiselect tag
    list — that is the SST-1 + SST-4 invariant we want to lock in.
    """
    logger.info("--- session_state persistence check ---")
    await page.goto(BASE_URL, wait_until="domcontentloaded")
    await _wait_streamlit_idle(page)
    seq_input = page.locator(
        '[data-testid="stMultiSelect"]:has(label:has-text("Sequence type")) input'
    ).first
    await seq_input.click()
    await page.locator('div[role="option"]:has-text("ChIP-Seq")').first.click()
    # Click outside to close the dropdown so the tag chip becomes visible.
    await page.locator("body").click()
    await page.wait_for_timeout(500)
    result: dict[str, object] = {"home_set_chip_seq": True}
    for label, url in [("gap_discovery", PAGES[1][1]), ("cohort", PAGES[2][1])]:
        await page.goto(url, wait_until="domcontentloaded")
        await _wait_streamlit_idle(page)
        await page.wait_for_timeout(1_000)
        tag_count = await page.locator(
            '[data-testid="stMultiSelect"]:has(label:has-text("Sequence type")) '
            'span:has-text("ChIP-Seq")'
        ).count()
        result[f"{label}_chip_seq_visible"] = tag_count > 0
        screenshot = OUT_DIR / f"persist_{label}.png"
        await page.screenshot(path=str(screenshot), full_page=True)
        result[f"{label}_screenshot"] = str(screenshot)
    return result


async def _run(p: Playwright) -> None:
    browser = await p.chromium.launch(headless=True)
    context = await browser.new_context(viewport={"width": 1440, "height": 900})
    page = await context.new_page()
    results: list[dict[str, object]] = []
    for label, url in PAGES:
        res = await _smoke_one(page, label, url)
        results.append(res)
    persistence = await _verify_seq_type_persists(page)

    summary = {"pages": results, "persistence": persistence}
    (OUT_DIR / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(json.dumps(summary, indent=2, ensure_ascii=False))

    any_errors = any(r["errors"] for r in results)
    any_chip_radio = any(r["has_chip_atlas_radio"] for r in results)
    all_seq_filter = all(r["has_sequence_type_filter"] for r in results)
    persistence_ok = persistence.get("gap_discovery_chip_seq_visible") and (
        persistence.get("cohort_chip_seq_visible")
    )

    await context.close()
    await browser.close()

    if any_errors:
        logger.error("found %d page(s) with errors", sum(1 for r in results if r["errors"]))
    if any_chip_radio:
        logger.error("ChIP-Atlas radio still visible on at least one page (CHIP-3 fail)")
    if not all_seq_filter:
        logger.error("sequence_type filter missing on at least one page")
    if not persistence_ok:
        logger.error("sequence_type filter did not persist Home → Gap / Cohort")

    if any_errors or any_chip_radio or not all_seq_filter or not persistence_ok:
        sys.exit(1)


def main() -> int:
    async def _entry() -> None:
        async with async_playwright() as p:
            await _run(p)

    asyncio.run(_entry())
    return 0


if __name__ == "__main__":
    sys.exit(main())
