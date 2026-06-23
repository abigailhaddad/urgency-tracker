"""urgency_and_why.py — recent urgency contracts, the actual justification, and the dollars.

Joins three things into one table:
  - the WHY  — SAM.gov J&A notices citing urgency (FAR 6.302-2), rationale text extracted
  - the HOW MUCH — the HuggingFace USAspending mirror, matched on PIID (no auth, no limit)
  - a BRIDGE flag — the most common urgency pattern (extending an expiring contract)

SAM.gov's search API is rate-limited, so this is built to survive it:
  - every notice it processes is cached under data/cache/<noticeId>.json — re-runs reuse it
  - --max-downloads caps how many *new* attachments it pulls per run
  - if SAM rate-limits, it stops, keeps everything cached, and you re-run to continue

Run:
    SAM_API_KEY=... python urgency_and_why.py --days 60 --year 2026
    SAM_API_KEY=... python urgency_and_why.py --days 60 --max-downloads 25   # gentle on the quota
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path

import requests

from fetch_justification import AUTHORITY_RE, URGENCY_RE, _extract_text, search_jna

CACHE = Path("data/cache")
HF = "https://huggingface.co/datasets/abigailhaddad/usaspending-bulk-awards/resolve/main/serve/contracts"
BRIDGE_RE = re.compile(r"\b(bridge|extension|extend|continuity of services|stop-?gap)\b", re.I)


def norm_piid(piid: str | None) -> str:
    """SAM writes 'W91CRB-25-C-A018'; the USAspending mirror stores 'W91CRB25CA018'. Strip to match."""
    return re.sub(r"[^A-Z0-9]", "", (piid or "").upper())


def hf_urgency_dollars(year: int) -> dict[str, dict]:
    """normalized PIID -> {recipient, sub_agency, dollars} for the year's urgency contracts (free, no auth)."""
    import duckdb
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    rows = con.execute(f"""
      SELECT award_id_piid, any_value(recipient_name), any_value(awarding_sub_agency_name),
             sum(federal_action_obligation)
      FROM read_parquet('{HF}/{year}.parquet')
      WHERE other_than_full_and_open_competition ILIKE '%URGENCY%'
      GROUP BY 1
    """).fetchall()
    return {norm_piid(r[0]): {"recipient": r[1], "sub_agency": r[2], "dollars": r[3]} for r in rows}


def process_notice(opp: dict, downloads_left: list[int]) -> dict | None:
    """Cached extraction for one J&A notice. Downloads attachments only if budget remains."""
    nid = opp.get("noticeId")
    cache_file = CACHE / f"{nid}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text())

    if downloads_left[0] <= 0:
        return None  # hit the per-run cap; leave for next run

    text = ""
    for link in opp.get("resourceLinks") or []:
        try:
            resp = requests.get(link, timeout=60)
            if resp.ok:
                text += "\n" + _extract_text(resp.content)
        except requests.RequestException:
            continue
    downloads_left[0] -= 1

    authorities = sorted({re.sub(r"\s+", " ", a).upper() for a in AUTHORITY_RE.findall(text)})
    rec = {
        "notice_id": nid,
        "title": opp.get("title", ""),
        "piid": (opp.get("award") or {}).get("number"),
        "agency": opp.get("fullParentPathName", ""),
        "posted": opp.get("postedDate", ""),
        "authorities": authorities,
        "is_urgency": bool(URGENCY_RE.search(text)) or any("6.302-2" in a for a in authorities),
        "is_bridge": bool(BRIDGE_RE.search(opp.get("title", "") + " " + text[:2000])),
        "rationale": re.sub(r"\s+", " ", text).strip()[:600],
        "link": opp.get("uiLink", ""),
    }
    CACHE.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(rec))
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=60, help="J&A look-back window (default 60)")
    ap.add_argument("--year", type=int, default=2026, help="HF fiscal year to join dollars from")
    ap.add_argument("--max-downloads", type=int, default=200, help="cap on NEW attachment pulls per run")
    ap.add_argument("--out", default="urgency_and_why.csv")
    args = ap.parse_args()

    api_key = os.environ.get("SAM_API_KEY")
    if not api_key:
        sys.exit("Set SAM_API_KEY (free from SAM.gov → Account Details → API Key).")

    try:
        notices = search_jna(api_key, args.days)
    except requests.HTTPError as e:
        sys.exit(f"SAM search failed (rate limit?): {e}. Try again later — cached work is preserved.")
    print(f"{len(notices)} J&A notices in the last {args.days} days. Extracting (cache + cap)…")

    budget = [args.max_downloads]
    records = []
    for opp in notices:
        rec = process_notice(opp, budget)
        if rec and rec["is_urgency"]:
            records.append(rec)
    print(f"{len(records)} urgency justifications (and {args.max_downloads - budget[0]} new downloads this run).")

    print(f"Joining dollars from the HF mirror (FY{args.year})…")
    dollars = hf_urgency_dollars(args.year)

    for r in records:
        hit = dollars.get(norm_piid(r["piid"])) if r["piid"] else None
        r["recipient"] = hit["recipient"] if hit else ""
        r["dollars"] = hit["dollars"] if hit else None
    records.sort(key=lambda r: (r["dollars"] or 0), reverse=True)

    with open(args.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["piid", "recipient", "agency", "dollars", "is_bridge", "authorities", "title", "rationale", "link"])
        for r in records:
            w.writerow([r["piid"], r["recipient"], r["agency"], r["dollars"], r["is_bridge"],
                        "; ".join(r["authorities"]), r["title"], r["rationale"], r["link"]])
    print(f"\nWrote {len(records)} rows to {args.out}.  Top by dollars:\n")
    for r in records[:10]:
        d = f"${r['dollars']/1e6:,.1f}M" if r["dollars"] else "    (no $ match)"
        flag = " [BRIDGE]" if r["is_bridge"] else ""
        print(f"  {d:>16}  {(r['recipient'] or r['title'])[:38]:38}{flag}")
        print(f"                    why: {r['rationale'][:90]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
