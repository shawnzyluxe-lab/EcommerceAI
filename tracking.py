"""Carrier detection and tracking URL generation for shipment tracking numbers."""
import re
from urllib.parse import quote


CARRIER_PATTERNS = [
    ("usps", r"^(?:94001|92055|9400|9407|9303|93033|92700|9[2-5]\d{18,20}|\d{20,22}|(?:EA|CP|RA|RC|RR|RZ)\d{9}(?:US|CN|HK|CA|GB|DE|AU))$"),
    ("ups", r"^(?:1Z[A-Z0-9]{16}|\d{9}|\d{12})$"),
    ("fedex", r"^(?:\d{12}|\d{15}|\d{20})$"),
    ("dhl", r"^(?:\d{10}|\d{11}|JJD\d{10,14}|\d{13})$"),
    ("ontrac", r"^[A-Za-z]\d{14}$"),
    ("amazon", r"^(?:TBA|TBC|TBM|TBB)\d{12,14}$"),
    ("lasership", r"^LX\d{10}$"),
]


CARRIER_URLS = {
    "usps": "https://tools.usps.com/go/TrackConfirmAction?tLabels={number}",
    "ups": "https://www.ups.com/track?tracknum={number}",
    "fedex": "https://www.fedex.com/fedextrack/?trknbr={number}",
    "dhl": "https://www.dhl.com/en/express/tracking.html?AWB={number}&brand=DHL",
    "ontrac": "https://www.ontrac.com/tracking/?number={number}",
    "amazon": "https://track.amazon.com/tracking/{number}",
    "lasership": "https://www.lasership.com/track/{number}",
}


def normalize_tracking_number(number: str) -> str:
    """Remove spaces and dashes from a tracking number."""
    return re.sub(r"[\s-]", "", number or "")


def detect_carrier(number: str) -> str:
    """Detect the carrier from a tracking number, or return an empty string."""
    clean = normalize_tracking_number(number).upper()
    if not clean:
        return ""
    for carrier, pattern in CARRIER_PATTERNS:
        if re.match(pattern, clean, re.IGNORECASE):
            return carrier
    return ""


def tracking_url(number: str, carrier: str = "") -> str:
    """Return a carrier tracking URL for a tracking number, or an empty string if unknown."""
    clean = normalize_tracking_number(number)
    if not clean:
        return ""
    detected = carrier or detect_carrier(clean)
    if not detected:
        return ""
    template = CARRIER_URLS.get(detected)
    if not template:
        return ""
    return template.format(number=quote(clean))
