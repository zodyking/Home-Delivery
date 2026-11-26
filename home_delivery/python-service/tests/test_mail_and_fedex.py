"""Mail-history dates, envelope parties, and FedEx page parsing."""

from __future__ import annotations

import importlib.util
from email.message import EmailMessage
from pathlib import Path

from carrier_detect import (
    IMPLEMENTED_CARRIERS,
    auto_detect_carrier_order,
    get_tracking_url,
)
from mail.informed_delivery import (
    _imap_search,
    _is_digest_message,
    message_delivery_date,
)
from mail.letter_ocr import _apply_hints, parse_parties_from_text, parties_from_ocr_lines

ROOT = Path(__file__).resolve().parents[1]


def _load_fedex_html():
    """Load the parser without importing scrapers/__init__.py (Playwright)."""
    path = ROOT / "scrapers" / "fedex_html.py"
    spec = importlib.util.spec_from_file_location("fedex_html", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _digest(subject: str, date_header: str, html: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["Date"] = date_header
    msg["From"] = "USPS Informed Delivery <USPSInformeddelivery@informeddelivery.usps.com>"
    msg.set_content("Daily Digest")
    msg.add_alternative(html, subtype="html")
    return msg


def test_header_date_stays_on_the_civil_day():
    """A late-evening Eastern digest must not roll onto the next UTC day."""
    msg = _digest(
        "Your Daily Digest",
        "Thu, 26 Nov 2026 23:40:00 -0500",
        "<html><body>Informed Delivery</body></html>",
    )
    assert message_delivery_date(msg) == "2026-11-26"


def test_subject_date_beats_the_header():
    msg = _digest(
        "Your Daily Digest for Thu, Nov 26",
        "Fri, 27 Nov 2026 04:10:00 +0000",
        "<html><body>Informed Delivery daily digest</body></html>",
    )
    assert message_delivery_date(msg) == "2026-11-26"


def test_printed_deliveries_date_beats_the_header():
    msg = _digest(
        "USPS Informed Delivery",
        "Fri, 27 Nov 2026 04:10:00 +0000",
        "<html><body>Today's Deliveries: 11/21/2026 mailpiece</body></html>",
    )
    assert message_delivery_date(msg) == "2026-11-21"


def test_two_digests_keep_separate_days():
    thursday = _digest(
        "Your Daily Digest for Thu, Nov 26",
        "Thu, 26 Nov 2026 07:00:00 -0500",
        "<html><body>mailpiece</body></html>",
    )
    friday = _digest(
        "Your Daily Digest for Fri, Nov 27",
        "Fri, 27 Nov 2026 07:00:00 -0500",
        "<html><body>mailpiece</body></html>",
    )
    assert message_delivery_date(thursday) != message_delivery_date(friday)
    assert _is_digest_message(thursday)
    assert _is_digest_message(friday)


def test_imap_search_does_not_nest_or():
    class FakeImap:
        def __init__(self) -> None:
            self.queries: list[str] = []

        def search(self, charset, query):
            assert charset is None
            self.queries.append(query)
            index = str(len(self.queries)).encode()
            return "OK", [index]

    imap = FakeImap()
    ids = _imap_search(imap, "01-Nov-2026", ignore_sender=False)
    assert [item.decode() for item in ids] == ["1", "2", "3", "4"]
    assert all(" OR " not in query for query in imap.queries)
    assert any("FROM " in query for query in imap.queries)
    assert any("Daily Digest" in query for query in imap.queries)


def test_all_caps_envelope_parses_both_parties():
    text = "\n".join(
        [
            "CAPITAL ONE",
            "PO BOX 31254",
            "SALT LAKE CITY UT 84131",
            "JANE Q RESIDENT",
            "443 LINWOOD AVE",
            "BROOKLYN NY 11208",
        ]
    )
    parties = parse_parties_from_text(text)
    assert parties["from_name"] == "CAPITAL ONE"
    assert "PO BOX 31254" in parties["from_address"]
    assert "SALT LAKE CITY UT 84131" in parties["from_address"]
    assert parties["to_name"] == "JANE Q RESIDENT"
    assert "443 LINWOOD AVE" in parties["to_address"]
    assert "BROOKLYN NY 11208" in parties["to_address"]


def test_position_split_separates_return_and_recipient():
    lines = [
        {"text": "CAPITAL ONE", "x": 40, "y": 30, "h": 18},
        {"text": "PO BOX 31254", "x": 40, "y": 52, "h": 18},
        {"text": "SALT LAKE CITY UT 84131", "x": 40, "y": 74, "h": 18},
        {"text": "JANE Q RESIDENT", "x": 80, "y": 220, "h": 20},
        {"text": "443 LINWOOD AVE", "x": 80, "y": 244, "h": 20},
        {"text": "BROOKLYN NY 11208", "x": 80, "y": 268, "h": 20},
    ]
    parties = parties_from_ocr_lines(
        lines,
        width=800,
        height=400,
        known_recipient="443 Linwood Ave",
    )
    assert parties["from_name"] == "CAPITAL ONE"
    assert parties["to_name"] == "JANE Q RESIDENT"
    assert "LINWOOD" in parties["to_address"]


def test_known_street_fills_a_blank_recipient():
    parties = parse_parties_from_text("ACME SUPPLY")
    _apply_hints(parties, known_recipient="443 Linwood Ave")
    assert parties["from_name"] == "ACME SUPPLY"
    assert parties["to_address"] == "443 Linwood Ave"


def test_fedex_url_uses_only_the_tracking_number():
    url = get_tracking_url("fedex", "385 3247-40657")
    assert url == "https://www.fedex.com/fedextrack/?trknbr=385324740657"
    assert "trkqual" not in url
    assert auto_detect_carrier_order("385324740657") == ["fedex"]
    assert "fedex" in IMPLEMENTED_CARRIERS


_FEDEX_FIXTURE = """
<html><body>
<p>TRACKING NUMBER</p><p>385324740657</p>
<p>SHIP DATE</p><p>Lakeland, FL US</p><p>11/23/2026</p>
<p>DELIVERY DETAILS</p><p>11/27/2026 before 8:00 pm</p>
<p>SERVICE</p><p>FedEx 2Day</p>
<table>
<tr>
  <td class="travel-history-table__scan-event-date"><span>Wednesday, 11/25/2026</span></td>
  <td>
    <div class="travel-history__scan-event">
      <div>4:17 AM</div>
      <span id="status">Arrived at FedEx hub</span>
      <div>KEASBEY, NJ</div>
    </div>
  </td>
</tr>
<tr>
  <td class="travel-history-table__scan-event-date"><span>Tuesday, 11/24/2026</span></td>
  <td>
    <div class="travel-history__scan-event">
      <div>10:05 PM</div>
      <span id="status">Departed FedEx hub</span>
      <div>NEWARK, NJ</div>
    </div>
    <div class="travel-history__scan-event">
      <div>10:57 AM</div>
      <span id="status">Arrived at FedEx hub</span>
      <div>NEWARK, NJ</div>
    </div>
  </td>
</tr>
<tr>
  <td class="travel-history-table__scan-event-date"><span>Monday, 11/23/2026</span></td>
  <td>
    <div class="travel-history__scan-event">
      <div>7:38 PM</div>
      <span id="status">Left FedEx origin facility</span>
      <div>LAKELAND, FL</div>
    </div>
    <div class="travel-history__scan-event">
      <div>4:13 PM</div>
      <span id="status">Picked up</span>
      <div>LAKELAND, FL</div>
    </div>
    <div class="travel-history__scan-event">
      <div>11:26 AM</div>
      <span id="status">Shipment information sent to FedEx</span>
    </div>
  </td>
</tr>
</table>
</body></html>
"""


def test_fedex_detail_page_parses_history_newest_first():
    parsed = _load_fedex_html().parse_fedex_html(_FEDEX_FIXTURE, "385324740657")
    assert not parsed["error"]
    assert parsed["status"] == "Arriving Friday"
    assert "before 8:00 pm" in parsed["status_detail"]
    assert "KEASBEY, NJ" in parsed["status_detail"]
    assert "FedEx 2Day" in parsed["status_detail"]
    assert len(parsed["events"]) == 6
    newest = parsed["events"][0]
    assert newest["description"] == "Arrived at FedEx hub"
    assert newest["location"] == "KEASBEY, NJ"
    assert newest["time"] == "4:17 AM"
    assert parsed["events"][-1]["description"] == "Shipment information sent to FedEx"
    assert parsed["events"][-1]["location"] == ""


def test_saved_fedex_detail_page_when_present():
    path = Path("/tmp/fedex.html")
    if not path.exists():
        return
    parsed = _load_fedex_html().parse_fedex_html(path.read_text(errors="replace"), "385324740657")
    assert parsed["status"] == "Arriving Friday"
    assert "before 8:00 pm" in parsed["status_detail"]
    assert "KEASBEY" in parsed["status_detail"].upper()
    assert len(parsed["events"]) == 6
    assert parsed["events"][0]["description"] == "Arrived at FedEx hub"
