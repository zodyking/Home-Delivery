"""
Parse a rendered FedEx tracking page.

The public URL is https://www.fedex.com/fedextrack/?trknbr=<number>.
Scan history is not in the summary card — it appears only after "View more
details", in the travel-history table on the detailed tracking page.
"""
from __future__ import annotations

import re
from datetime import datetime
from html.parser import HTMLParser
from typing import Any


_NOT_FOUND_RE = re.compile(
    r"tracking number (?:you entered )?cannot be found"
    r"|we (?:could not|couldn't|cannot|can't) find"
    r"|no (?:record|results?) (?:of|for) this"
    r"|please check (?:your )?tracking number",
    re.IGNORECASE,
)
_SLASH_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{2,4})")
_TIME_RE = re.compile(r"\b(\d{1,2}:\d{2}\s*[AP]M)\b", re.IGNORECASE)
_TIME_PHRASE_RE = re.compile(
    r"\b((?:before|by|after)\s+\d{1,2}:\d{2}\s*[AP]\.?M\.?)\b",
    re.IGNORECASE,
)
_FACT_LABELS = {
    "TRACKING NUMBER",
    "SHIP DATE",
    "DELIVERY DETAILS",
    "ESTIMATED DELIVERY",
    "SCHEDULED DELIVERY",
    "ACTUAL DELIVERY",
    "SERVICE",
    "STATUS",
}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _parse_slash_date(text: str) -> datetime | None:
    match = _SLASH_DATE_RE.search(text or "")
    if not match:
        return None
    month, day, year = (int(part) for part in match.groups())
    if year < 100:
        year += 2000
    try:
        return datetime(year, month, day)
    except ValueError:
        return None


def _parse_event_datetime(date_text: str, time_text: str) -> datetime | None:
    day = _parse_slash_date(date_text)
    if not day:
        return None
    match = _TIME_RE.search(time_text or "")
    if not match:
        return day
    try:
        clock = datetime.strptime(match.group(1).upper().replace(" ", ""), "%I:%M%p")
    except ValueError:
        return day
    return day.replace(hour=clock.hour, minute=clock.minute)


def _class_set(attrs: list[tuple[str, str | None]]) -> set[str]:
    raw = dict(attrs).get("class") or ""
    return set(raw.split())


class _HistoryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[dict[str, Any]]] = []
        self._skip = 0
        self._depth = 0
        self._rows: list[dict[str, Any]] | None = None
        self._saw_event = False
        self._row: dict[str, Any] | None = None
        self._in_date = False
        self._date_parts: list[str] = []
        self._in_event = False
        self._event_parts: list[str] = []
        self._status_parts: list[str] = []
        self._in_status = False
        self._time_parts: list[str] = []
        self._in_time = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "svg"}:
            self._skip += 1
            return
        if self._skip:
            return
        classes = _class_set(attrs)
        if self._in_event and "travel-history__scan-event" in classes:
            self._close_event()
        if tag == "table":
            self._depth += 1
            if self._depth == 1:
                self._rows = []
                self._saw_event = False
        if self._rows is None:
            return
        if tag == "tr" and self._depth == 1:
            self._row = {"date": "", "events": []}
        if "travel-history-table__scan-event-date" in classes:
            self._in_date = True
            self._date_parts = []
        if "travel-history__scan-event" in classes:
            self._saw_event = True
            self._in_event = True
            self._event_parts = []
            self._status_parts = []
            self._time_parts = []
            self._in_status = False
            self._in_time = False
        element_id = dict(attrs).get("id") or ""
        if self._in_event and element_id == "status":
            self._in_status = True
        elif self._in_event and tag == "span" and not self._in_status and not self._time_parts:
            self._in_time = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "svg"} and self._skip:
            self._skip -= 1
            return
        if self._skip:
            return
        if tag == "span" and self._in_time:
            self._in_time = False
        if tag == "span" and self._in_status:
            self._in_status = False
        if tag == "td" and self._in_date:
            if self._row is not None:
                self._row["date"] = _clean(" ".join(self._date_parts))
            self._in_date = False
        if tag == "tr" and self._row is not None and self._rows is not None:
            self._close_event()
            if self._row["date"] or self._row["events"]:
                self._rows.append(self._row)
            self._row = None
        if tag == "table" and self._depth:
            self._close_event()
            self._depth -= 1
            if self._depth == 0:
                if self._saw_event and self._rows:
                    self.tables.append(self._rows)
                self._rows = None
                self._saw_event = False

    def handle_data(self, data: str) -> None:
        if self._skip or not data or not data.strip():
            return
        if self._in_date:
            self._date_parts.append(data)
        if self._in_event:
            self._event_parts.append(data)
        if self._in_status:
            self._status_parts.append(data)
        if self._in_time:
            self._time_parts.append(data)

    def _close_event(self) -> None:
        if not self._in_event or self._row is None:
            return
        texts = [_clean(part) for part in self._event_parts]
        texts = [part for part in texts if part]
        status = _clean(" ".join(self._status_parts))
        time_text = _clean(" ".join(self._time_parts))
        match = _TIME_RE.search(time_text)
        time_text = match.group(1).upper() if match else ""
        if not time_text:
            for part in texts:
                found = _TIME_RE.search(part)
                if found:
                    time_text = found.group(1).upper()
                    break
        if not status:
            for part in texts:
                if _TIME_RE.fullmatch(part) or (time_text and part.upper() == time_text):
                    continue
                status = part
                break
        location = ""
        for part in reversed(texts):
            if not part or part == status:
                continue
            if time_text and part.upper() == time_text:
                continue
            if _TIME_RE.fullmatch(part):
                continue
            location = part
            break
        if status:
            self._row["events"].append({
                "time": time_text,
                "status": status,
                "location": location,
            })
        self._in_event = False
        self._in_status = False
        self._in_time = False


def _visible_lines(html: str) -> list[str]:
    stripped = re.sub(r"(?is)<(script|style|svg)\b[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", stripped)
    text = re.sub(r"<[^>]+>", "\n", text)
    lines = [_clean(line) for line in text.splitlines()]
    return [line for line in lines if line]


def _shipment_facts(lines: list[str]) -> dict[str, str]:
    facts: dict[str, str] = {}
    index = 0
    while index < len(lines):
        label = lines[index].upper().rstrip(":")
        if label in _FACT_LABELS and label not in facts:
            value = ""
            for follower in lines[index + 1 : index + 5]:
                if follower.upper().rstrip(":") in _FACT_LABELS:
                    break
                if len(follower) > 80:
                    continue
                value = follower
                break
            if value:
                facts[label] = value
        index += 1
    return facts


def _choose_table(tables: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    def score(rows: list[dict[str, Any]]) -> tuple[int, int]:
        events = sum(len(row.get("events") or []) for row in rows)
        dated = " ".join(row.get("date") or "" for row in rows)
        four_digit = 1 if re.search(r"/\d{4}\b", dated) else 0
        return (four_digit, events)

    return max(tables, key=score)


def _status_from_facts(
    facts: dict[str, str],
    latest_status: str,
    latest_location: str,
    lines: list[str],
) -> tuple[str, str]:
    banner = ""
    for line in lines:
        if len(line) > 80:
            continue
        if re.match(r"^(Delivered|Out for delivery|Arriving|On the way|Label created)\b", line, re.I):
            banner = line
            break

    low = f"{banner} {latest_status}".lower()
    delivery = (
        facts.get("DELIVERY DETAILS")
        or facts.get("ESTIMATED DELIVERY")
        or facts.get("SCHEDULED DELIVERY")
        or facts.get("ACTUAL DELIVERY")
        or ""
    )
    if banner and re.match(r"^(Delivered|Out for delivery|Arriving)\b", banner, re.I):
        status = banner
    elif "delivered" in low and "out for delivery" not in low:
        status = "Delivered"
    elif "out for delivery" in low or "on fedex vehicle" in low or "on vehicle for delivery" in low:
        status = "Out for delivery"
    else:
        when = _parse_slash_date(delivery)
        status = f"Arriving {when.strftime('%A')}" if when else (latest_status or "In transit")

    detail_parts: list[str] = []
    phrase = _TIME_PHRASE_RE.search(delivery)
    if phrase:
        detail_parts.append(phrase.group(1))
    elif delivery and delivery.lower() not in status.lower():
        detail_parts.append(delivery)
    current = ""
    for line in lines:
        match = re.search(r"Currently in\s+(.+)", line, re.I)
        if match:
            current = _clean(match.group(1))
            break
    if not current and latest_location and "delivered" not in status.lower():
        current = latest_location
    if current:
        detail_parts.append(f"Currently in {current}")
    service = facts.get("SERVICE") or ""
    if service and service.lower() not in status.lower():
        detail_parts.append(service)
    return status, " · ".join(detail_parts)


def parse_fedex_html(html: str, tracking_number: str = "") -> dict[str, Any]:
    """
    Parse FedEx detailed-tracking HTML.

    Returns a dict with status, status_detail, and events (newest first).
    `error` is set when the page is a not-found or empty result.
    """
    if not html:
        return {"error": "FedEx tracking content not found", "events": []}

    lines = _visible_lines(html)
    blob = "\n".join(lines)
    if _NOT_FOUND_RE.search(blob) and "travel-history__scan-event" not in html:
        return {"error": "FedEx tracking number not found", "events": []}

    parser = _HistoryParser()
    parser.feed(html)
    parser.close()

    events: list[dict[str, Any]] = []
    if parser.tables:
        for row in _choose_table(parser.tables):
            day = row.get("date") or ""
            for item in row.get("events") or []:
                description = item.get("status") or ""
                if not description:
                    continue
                events.append({
                    "date": day,
                    "time": item.get("time") or "",
                    "description": description,
                    "location": item.get("location") or "",
                    "status": description,
                    "detail": item.get("location") or "",
                    "_sort": _parse_event_datetime(day, item.get("time") or ""),
                })

    events.sort(key=lambda event: event.get("_sort") or datetime.min, reverse=True)
    for event in events:
        event.pop("_sort", None)

    # Two copies of the table can still describe the same scans. Drop dupes.
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for event in events:
        key = (event.get("date", ""), event.get("time", ""), event.get("description", ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(event)
    events = unique

    latest = events[0] if events else {}
    facts = _shipment_facts(lines)
    status, status_detail = _status_from_facts(
        facts,
        latest.get("description") or "",
        latest.get("location") or "",
        lines,
    )

    if tracking_number and facts.get("TRACKING NUMBER"):
        found = re.sub(r"\s+", "", facts["TRACKING NUMBER"])
        if found and found not in re.sub(r"\s+", "", tracking_number):
            # A different shipment was rendered. Still return it; the caller
            # asked for this number and FedEx sometimes formats it with spaces.
            pass

    if not events and not status:
        return {"error": "FedEx tracking content not found", "events": []}

    return {
        "status": status,
        "status_detail": status_detail,
        "events": events,
        "error": None,
    }
