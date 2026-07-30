"""
DOM parsers for carrier tracking pages (executed in-browser via Playwright).
"""
from __future__ import annotations

import logging
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
  const statusLine = Array.from(document.querySelectorAll("p, span, strong, div"))
    .map((el) => (el.innerText || "").trim())
    .find((text) => /^(Delivered|Out for Delivery|On the Way|Label Created|We Have Your Package)/i.test(text));
  const detailLine = Array.from(document.querySelectorAll("p, span, div"))
    .map((el) => (el.innerText || "").trim())
    .find((text) => /Left at|Front Door|Mailbox|Delivered To|Out For Delivery Today/i.test(text));
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


async def expand_ups_details(page: Page) -> None:
    """Open UPS Package History (Show Details)."""
    for locator in (
        page.get_by_role("button", name="Show Details"),
        page.locator("button:has-text('Show Details')"),
    ):
        if await locator.count() == 0:
            continue
        try:
            await locator.first.click()
            await page.wait_for_selector("#shipProg_act_Date0", timeout=15000)
            await page.wait_for_timeout(800)
            logger.debug("UPS Show Details opened")
            return
        except Exception as exc:
            logger.debug("UPS Show Details click failed: %s", exc)

    # Already expanded if Hide Details is visible.
    if await page.locator("button:has-text('Hide Details')").count() > 0:
        await page.wait_for_selector("#shipProg_act_Date0", timeout=10000)
        return


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

    events = await page.evaluate(UPS_PARSE_EVENTS_JS)
    summary = await page.evaluate(UPS_PARSE_SUMMARY_JS)

    status = ""
    status_detail = ""
    if events:
        status = events[0].get("milestone") or events[0].get("description", "")
        status_detail = events[0].get("activity") or ""
    else:
        status = summary.get("status", "")
        status_detail = summary.get("detail", "")

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
