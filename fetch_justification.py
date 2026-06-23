"""Pull the actual Justification & Approval (J&A) text from SAM.gov.

USAspending tells you the *reason code* for a non-competed award (e.g. URGENCY).
It does not tell you *why*. Under FAR 6.305, the agency must post the J&A
document — and those land on SAM.gov as "Justification" notices. This script
pulls recent ones, extracts the rationale text, and flags the urgency cites.

It needs a free SAM.gov API key in the SAM_API_KEY environment variable
(SAM.gov → Account Details → API Key). This is a separate, keyed source — unlike
the HuggingFace mirror used by demo.ipynb, which is free and needs no auth.

Usage:
    SAM_API_KEY=... python fetch_justification.py --days 30
    SAM_API_KEY=... python fetch_justification.py --days 90 --urgency-only
    SAM_API_KEY=... python fetch_justification.py --days 90 --piid 70Z02326C93210002
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys
from datetime import date, timedelta

import requests

OPP_SEARCH = "https://api.sam.gov/prod/opportunities/v2/search"
JUSTIFICATION_PTYPE = "u"  # SAM notice-type code that now covers J&A / limited-sources justifications

# Authorities that show up in sole-source justifications; 6.302-2 is the urgency one.
AUTHORITY_RE = re.compile(
    r"(FAR\s*6\.302-\d+(?:\([a-z0-9)(]+)?|FAR\s*8\.405-6|FAR\s*13\.\d+|FAR\s*16\.505|"
    r"VAAR\s*8\d{2}\.\d+|DFARS\s*2\d{2}\.\d+|10\s*U\.?S\.?C\.?\s*32\d{2}|41\s*U\.?S\.?C\.?\s*\d+)",
    re.I,
)
URGENCY_RE = re.compile(r"6\.302-2|unusual and compelling urgency", re.I)


def _extract_text(content: bytes) -> str:
    """Best-effort text from a PDF attachment."""
    try:
        from pypdf import PdfReader
    except ImportError:
        raise SystemExit("pypdf missing — pip install pypdf")
    try:
        reader = PdfReader(io.BytesIO(content))
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception:
        return ""


def search_jna(api_key: str, days: int) -> list[dict]:
    """Return J&A opportunity notices posted in the last `days` days."""
    today = date.today()
    params = {
        "api_key": api_key,
        "ptype": JUSTIFICATION_PTYPE,
        "postedFrom": (today - timedelta(days=days)).strftime("%m/%d/%Y"),
        "postedTo": today.strftime("%m/%d/%Y"),
        "limit": 1000,
        "offset": 0,
    }
    out: list[dict] = []
    while True:
        r = requests.get(OPP_SEARCH, params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
        page = data.get("opportunitiesData", [])
        out.extend(page)
        if len(page) < params["limit"]:
            break
        params["offset"] += params["limit"]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=30, help="look-back window in days (default 30)")
    ap.add_argument("--urgency-only", action="store_true", help="only notices citing FAR 6.302-2 urgency")
    ap.add_argument("--piid", help="only the notice whose award number matches this PIID")
    ap.add_argument("--limit", type=int, default=25, help="max notices to print")
    args = ap.parse_args()

    api_key = os.environ.get("SAM_API_KEY")
    if not api_key:
        sys.exit("Set SAM_API_KEY (free from SAM.gov → Account Details → API Key).")

    notices = search_jna(api_key, args.days)
    print(f"{len(notices)} J&A notices posted in the last {args.days} days.\n")

    shown = 0
    for opp in notices:
        piid = (opp.get("award") or {}).get("number")
        if args.piid and piid != args.piid:
            continue

        # Pull and concatenate the attachment text (resourceLinks are free S3 URLs).
        text = ""
        for link in opp.get("resourceLinks") or []:
            try:
                resp = requests.get(link, timeout=60)
                if resp.ok:
                    text += "\n" + _extract_text(resp.content)
            except requests.RequestException:
                continue

        authorities = sorted({re.sub(r"\s+", " ", a).upper() for a in AUTHORITY_RE.findall(text)})
        is_urgency = bool(URGENCY_RE.search(text)) or any("6.302-2" in a for a in authorities)
        if args.urgency_only and not is_urgency:
            continue

        snippet = re.sub(r"\s+", " ", text).strip()[:400]
        print("=" * 80)
        print(f"{opp.get('title', '(no title)')}")
        print(f"  agency:      {opp.get('fullParentPathName', '')}")
        print(f"  posted:      {opp.get('postedDate', '')}   award PIID: {piid or '(none)'}")
        print(f"  authorities: {', '.join(authorities) or '(none found in text)'}")
        print(f"  urgency:     {is_urgency}")
        print(f"  rationale:   {snippet or '(no extractable text / redacted)'}")
        print(f"  link:        {opp.get('uiLink', '')}")
        shown += 1
        if shown >= args.limit:
            break

    if shown == 0:
        print("No matching notices. Try a wider --days window or drop --urgency-only / --piid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
