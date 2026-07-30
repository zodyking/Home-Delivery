"""
DOM parsers for carrier tracking pages (executed in-browser via Playwright).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from playwright.async_api import Page

logger = logging.getLogger(__name__)

# Parse every UPS Package History row after Show Details is open.
UPS_PARSE_EVENTS_JS = """
() => {
  const events = [];
  for (let i = 0; i < 100; i++) {
    const dateEl = document.getElementById(`shipProg_act_Date${i}`);
    if (!dateEl) break;

    const timeEl = document.getElementById(`shipProg_act_Time${i}`);
    const milestoneEl = document.getElementById(`shipProg_act_Milestone${i}`);
    const activityEl = document.getElementById(`shipProg_act_ActivityScan${i}`);
    const locationEl = document.getElementById(`shipProg_act_Location${i}`);

    const milestone = milestoneEl?.innerText?.trim() || "";
    const activity = activityEl?.innerText?.trim() || "";
    const date = [dateEl?.innerText?.trim(), timeEl?.innerText?.trim()]
      .filter(Boolean)
      .join(" ");

    let description = milestone || activity;
    if (milestone && activity && milestone !== activity) {
      description = `${milestone} - ${activity}`;
    }

    if (description) {
      events.push({
        date,
        description,
        location: locationEl?.innerText?.trim() || "",
        milestone,
        activity,
      });
    }
  }
  return events;
}
"""

UPS_PARSE_SUMMARY_JS = """
() => {
  // Scope to tracking results — never scan global nav/footer (UPS Store "Mailbox").
  const root =
    document.querySelector("app-track-details") ||
    document.querySelector("main") ||
    document.body;

  const nodes = Array.from(
    root.querySelectorAll("h1, h2, h3, p, span, strong, div")
  );
  const texts = nodes
    .map((el) => (el.innerText || "").trim().replace(/\\s+/g, " "))
    .filter((text) => text && text.length < 120);

  const statusLine = texts.find((text) =>
    /^(Delivered|Out for Delivery|On the Way|Label Created|We Have Your Package)\\b/i.test(text)
  );
  const detailLine = texts.find((text) =>
    /^(Left at|Delivered To|Out For Delivery Today)\\b|Front Door|Garage|Porch|Side Door/i.test(text)
  );
  return {
    status: statusLine || "",
    detail: detailLine || "",
  };
}
"""

# Parse all USPS tb-step rows after history section is expanded.
USPS_PARSE_EVENTS_JS = """
() => {
  const events = [];
  document.querySelectorAll(".tb-step").forEach((step) => {
    if (step.querySelector(".expand-collapse-history")) return;

    const status = step.querySelector(".tb-status")?.innerText?.trim() || "";
    const detail = step.querySelector(".tb-status-detail")?.innerText?.trim() || "";
    const location = step.querySelector(".tb-location")?.innerText?.trim() || "";
    const date = step.querySelector(".tb-date")?.innerText?.trim() || "";
    const description = detail || status;

    if (description) {
      events.push({ date, description, location, status, detail });
    }
  });
  return events;
}
"""

USPS_EXPAND_HISTORY_JS = """
() => {
  const anchors = Array.from(document.querySelectorAll("a"));
  const historyLink =
    anchors.find((a) => /see all tracking history/i.test(a.innerText || a.textContent || "")) ||
    anchors.find((a) => /show tracking history/i.test(a.innerText || a.textContent || "")) ||
    anchors.find(
      (a) =>
        a.classList.contains("expand-collapse-history") &&
        /show|see all/i.test(a.innerText || a.textContent || ""),
    );

  if (!historyLink) return "none";

  const text = (historyLink.innerText || historyLink.textContent || "").trim();
  if (/hide tracking history/i.test(text)) return "already";

  historyLink.click();
  return "clicked";
}
"""


def _compute_fingerprint(events: list[dict[str, Any]]) -> str:
    import hashlib

    if not events:
        return ""
    latest = events[0]
    data = f"{latest.get('date', '')}{latest.get('description', '')}"
    return hashlib.md5(data.encode()).hexdigest()[:12]


def _is_out_for_delivery(status: str, status_detail: str = "") -> bool:
    combined = f"{status} {status_detail}".lower()
    return "out for delivery" in combined


def _is_delivered(status: str, status_detail: str = "") -> bool:
    combined = f"{status} {status_detail}".lower()
    return "delivered" in combined and "out for delivery" not in combined


def build_tracking_result(
    events: list[dict[str, Any]],
    status: str,
    status_detail: str = "",
) -> dict[str, Any]:
    """Normalize parsed carrier events into the package poll payload."""
    if not status and events:
        first = events[0]
        status = first.get("milestone") or first.get("status") or first.get("description", "")
        status_detail = first.get("activity") or first.get("detail") or ""

    out_for_delivery = _is_out_for_delivery(status, status_detail)
    delivered = _is_delivered(status, status_detail)

    result: dict[str, Any] = {
        "status": status or status_detail or "Unknown",
        "status_detail": status_detail,
        "events": events,
        "last_event_fingerprint": _compute_fingerprint(events),
        "out_for_delivery": out_for_delivery,
        "delivered": delivered,
        "last_polled": datetime.now(timezone.utc).isoformat(),
        "error": None,
    }

    if delivered:
        result["delivered_at"] = datetime.now(timezone.utc).isoformat()

    return result


async def _dismiss_ups_overlays(page: Page) -> None:
    """Close cookie/chat/service-alert overlays that can block Show Details."""
    for selector in (
        'button[aria-label="Close All Service Alerts"]',
        'button:has-text("Dismiss All")',
        'button:has-text("Dismiss Service Alert")',
        '#onetrust-accept-btn-handler',
        'button:has-text("Accept All Cookies")',
        'button:has-text("Accept Cookies")',
        'button[aria-label="Close chat window"]',
        'button[aria-label="Minimize chat"]',
    ):
        loc = page.locator(selector)
        try:
            if await loc.count() == 0:
                continue
            await loc.first.click(timeout=1500, force=True)
            await page.wait_for_timeout(400)
        except Exception:
            continue

    # OneTrust / cookie banner sometimes only exposes a generic Close.
    try:
        dialog_close = page.locator(
            '[role="dialog"] button:has-text("Close"), '
            '[aria-label*="cookie" i] button:has-text("Close")'
        )
        if await dialog_close.count() > 0:
            await dialog_close.first.click(timeout=1500, force=True)
            await page.wait_for_timeout(300)
    except Exception:
        pass


async def expand_ups_details(page: Page) -> None:
    """Open UPS Package History (Show Details)."""
    await _dismiss_ups_overlays(page)

    if await page.locator("#shipProg_act_Date0").count() > 0:
        return

    # Already expanded.
    if await page.locator("button:has-text('Hide Details')").count() > 0:
        try:
            await page.wait_for_selector("#shipProg_act_Date0", timeout=10000)
        except Exception:
            pass
        return

    for locator in (
        page.get_by_role("button", name="Show Details"),
        page.locator("button:has-text('Show Details')"),
        page.locator("button:has(strong:has-text('Show Details'))"),
    ):
        if await locator.count() == 0:
            continue
        try:
            await locator.first.scroll_into_view_if_needed()
            await locator.first.click(timeout=5000)
            await page.wait_for_selector("#shipProg_act_Date0", timeout=15000)
            await page.wait_for_timeout(800)
            logger.debug("UPS Show Details opened")
            return
        except Exception as exc:
            logger.debug("UPS Show Details click failed: %s", exc)

    # Fallback: JS click (chat/cookie overlays can steal Playwright clicks).
    clicked = await page.evaluate(
        """() => {
          const candidates = Array.from(
            document.querySelectorAll("button, a, [role='button'], span, strong")
          );
          const el = candidates.find((node) =>
            /\\bShow Details\\b/i.test(
              (node.innerText || node.textContent || "").replace(/\\s+/g, " ")
            )
          );
          if (!el) return false;
          const target = el.closest("button, a, [role='button']") || el;
          target.click();
          return true;
        }"""
    )
    if clicked:
        try:
            await page.wait_for_selector("#shipProg_act_Date0", timeout=15000)
            await page.wait_for_timeout(800)
            logger.debug("UPS Show Details opened via JS click")
        except Exception as exc:
            logger.debug("UPS Show Details JS expand failed: %s", exc)


async def expand_usps_history(page: Page) -> None:
    """Open USPS full tracking history (See All Tracking History)."""
    state = await page.evaluate(USPS_EXPAND_HISTORY_JS)
    logger.debug("USPS history expand state: %s", state)

    if state != "clicked":
        return

    await page.wait_for_timeout(2000)
    try:
        await page.wait_for_function(
            """() => {
                const steps = document.querySelectorAll('.tb-step');
                return steps.length > 1 || document.querySelector('.tb-step .tb-status-detail');
            }""",
            timeout=10000,
        )
    except Exception:
        pass


async def parse_ups_tracking(page: Page) -> dict[str, Any]:
    """Expand UPS details and parse full package history."""
    await expand_ups_details(page)

    # One more beat if expand raced the Angular render.
    if await page.locator("#shipProg_act_Date0").count() == 0:
        await page.wait_for_timeout(1500)
        await expand_ups_details(page)

    events = await page.evaluate(UPS_PARSE_EVENTS_JS)
    summary = await page.evaluate(UPS_PARSE_SUMMARY_JS)

    status = ""
    status_detail = ""
    if events:
        status = events[0].get("milestone") or events[0].get("description", "")
        status_detail = events[0].get("activity") or ""
    else:
        status = (summary.get("status") or "").strip()
        status_detail = (summary.get("detail") or "").strip()
        # Never promote long nav blobs if summary still misfires.
        if status and ("\n" in status or len(status) > 80):
            status = ""
        if status_detail and ("\n" in status_detail or len(status_detail) > 120):
            status_detail = ""

    logger.info("UPS parsed %s history events", len(events))
    return build_tracking_result(events, status, status_detail)


async def parse_usps_tracking(page: Page) -> dict[str, Any]:
    """Expand USPS history and parse every tb-step event."""
    await expand_usps_history(page)

    events = await page.evaluate(USPS_PARSE_EVENTS_JS)

    status = ""
    status_detail = ""
    if events:
        status = events[0].get("status") or events[0].get("description", "")
        status_detail = events[0].get("detail") or ""

    logger.info("USPS parsed %s history events", len(events))
    return build_tracking_result(events, status, status_detail)


ESTES_PARSE_JS = """
() => {
  const clean = (text) => (text || "").replace(/\\s+/g, " ").trim();

  const statusCell = document.querySelector(
    "td.mat-column-status .tbl-header-status span, td.mat-column-status span"
  );
  let status = clean(statusCell?.innerText || "");

  // Prefer the active progress step label when present.
  const activeStep = document.querySelector(
    "ul.progressbar li.active .step-info, ul.progressbar li.active .active-name"
  );
  if (activeStep) {
    const stepText = clean(activeStep.innerText || "");
    if (stepText) status = stepText;
  }

  const statusDetail = clean(
    document.querySelector(".status-block .outfordelivery span, .status-block span")
      ?.innerText || ""
  );

  const estimated = clean(
    document.querySelector("td.mat-column-estimatedDelivery")?.innerText || ""
  );

  const events = [];
  document.querySelectorAll("dl.wizard dt").forEach((dt) => {
    const title = dt.querySelector(".lastTitle, span");
    const description = clean(title?.innerText || dt.innerText || "");
    if (!description) return;

    let date = "";
    const dd = dt.nextElementSibling;
    if (dd && dd.tagName === "DD") {
      date = Array.from(dd.querySelectorAll("small"))
        .map((el) => clean(el.innerText || ""))
        .filter(Boolean)
        .join(" ");
      if (!date) date = clean(dd.innerText || "");
    }

    // Optional date group label above the wizard list
    let groupDate = "";
    const group = dt.closest("div")?.querySelector?.(".groupLabel");
    if (group) groupDate = clean(group.innerText || "");

    events.push({
      date: date || groupDate,
      description,
      location: "",
      status: description,
      detail: "",
    });
  });

  return { status, statusDetail, estimated, events };
}
"""


async def expand_estes_details(page: Page) -> None:
    """
    Expand Estes result row + Shipment History accordion.

    Prefer "Expand All" (fastest), then row chevron + history panel.
    """
    try:
        expand_all = page.get_by_role("button", name=re.compile(r"Expand All", re.I))
        if await expand_all.count() > 0:
            await expand_all.first.click(timeout=2500)
            await page.wait_for_timeout(350)
        else:
            toggle = page.locator("td.mat-column-toggle, .mat-column-toggle").first
            if await toggle.count() > 0:
                down = toggle.locator(".fa-chevron-down")
                if await down.count() > 0:
                    await down.first.click(timeout=2500)
                else:
                    await toggle.click(timeout=2500)
                await page.wait_for_timeout(350)
    except Exception as exc:
        logger.debug("Estes expand click failed: %s", exc)

    # Ensure Shipment History panel is open if still collapsed.
    history_opened = await page.evaluate(
        """() => {
          const headers = Array.from(
            document.querySelectorAll("mat-expansion-panel-header, .mat-expansion-panel-header")
          );
          const history = headers.find((el) =>
            /shipment history/i.test(el.innerText || el.textContent || "")
          );
          if (!history) return "none";
          const panel = history.closest("mat-expansion-panel, .mat-expansion-panel");
          const expanded =
            history.classList.contains("mat-expanded") ||
            history.getAttribute("aria-expanded") === "true" ||
            panel?.classList?.contains("mat-expanded");
          if (expanded) return "already";
          history.click();
          return "clicked";
        }"""
    )
    logger.debug("Estes history expand state: %s", history_opened)
    if history_opened == "clicked":
        await page.wait_for_timeout(300)

    try:
        await page.wait_for_selector("dl.wizard dt, .status-block span, td.mat-column-status", timeout=4000)
    except Exception:
        pass


async def parse_estes_tracking(page: Page) -> dict[str, Any]:
    """Expand Estes details and parse shipment history events."""
    await expand_estes_details(page)

    parsed = await page.evaluate(ESTES_PARSE_JS)
    events = parsed.get("events") or []
    status = (parsed.get("status") or "").strip()
    status_detail = (parsed.get("statusDetail") or "").strip()
    estimated = (parsed.get("estimated") or "").strip()

    if not events and status:
        events = [{
            "date": "",
            "description": status_detail or status,
            "location": "",
            "status": status,
            "detail": status_detail,
        }]

    if estimated and status_detail:
        status_detail = f"{status_detail} · ETA {estimated}"
    elif estimated and not status_detail:
        status_detail = f"ETA {estimated}"

    logger.info("Estes parsed %s history events (status=%s)", len(events), status or "?")
    return build_tracking_result(events, status, status_detail)
