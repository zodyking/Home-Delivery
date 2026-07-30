"""
DOM parsers for carrier tracking pages (executed in-browser via Playwright).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from playwright.async_api import Page

# UPS: expand timeline then read shipProg_act_* nodes (see example MHTML).
UPS_PARSE_EVENTS_JS = """
() => {
  const events = [];
  for (let i = 0; i < 50; i++) {
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

# UPS headline before timeline expands (new tracking UI).
UPS_PARSE_SUMMARY_JS = """
() => {
  const deliveredBadge = document.body?.innerText?.match(/Delivered\\s+check_circle/i);
  const statusLine = Array.from(document.querySelectorAll("p, span, div"))
    .map((el) => (el.innerText || "").trim())
    .find((text) => /^(Delivered|Out for Delivery|On the Way|Label Created|We Have Your Package)/i.test(text));
  const detailLine = Array.from(document.querySelectorAll("p, span, div"))
    .map((el) => (el.innerText || "").trim())
    .find((text) => /Left at|Front Door|Mailbox|Delivered To/i.test(text));
  return {
    status: statusLine || (deliveredBadge ? "Delivered" : ""),
    detail: detailLine || "",
  };
}
"""

# USPS: tb-step timeline (see example MHTML).
USPS_PARSE_EVENTS_JS = """
async () => {
  const link = document.querySelector("a.expand-collapse-history");
  if (link && /show/i.test(link.textContent || "")) {
    link.click();
    await new Promise((resolve) => setTimeout(resolve, 900));
  }

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


async def parse_ups_tracking(page: Page) -> dict[str, Any]:
    """Expand UPS details drawer and parse shipment progress."""
    show_details = page.get_by_role("button", name="Show Details")
    if await show_details.count() == 0:
        show_details = page.locator("button:has-text('Show Details')")

    if await show_details.count() > 0:
        try:
            await show_details.first.click()
            await page.wait_for_selector("#shipProg_act_Date0", timeout=15000)
        except Exception:
            pass

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

    return build_tracking_result(events, status, status_detail)


async def parse_usps_tracking(page: Page) -> dict[str, Any]:
    """Expand USPS history and parse tb-step timeline."""
    events = await page.evaluate(USPS_PARSE_EVENTS_JS)

    status = ""
    status_detail = ""
    if events:
        status = events[0].get("status") or events[0].get("description", "")
        status_detail = events[0].get("detail") or ""

    return build_tracking_result(events, status, status_detail)
