"""validate_feed.py — guard rails on site/urgent.json before it ships.

Runs in CI after build_feed.py and BEFORE commit/deploy. If the data is malformed
or missing a field the front end (index.html) or the CSV export depends on, this
fails loudly so bad data never reaches the live site. Exits non-zero on any problem.

    python scripts/validate_feed.py
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "site" / "urgent.json"
JA_DIR = ROOT / "site" / "justifications"
JA_JSON = ROOT / "site" / "justifications.json"

# Every field the table rows, the detail modal, and the CSV export read. If one
# goes missing, the front end silently breaks — so require it on every award.
REQUIRED = [
    "piid", "recipient", "agency", "sub_agency", "funding_agency", "obligated",
    "date", "description", "category", "award_type", "urgency_type", "fair_opportunity",
    "naics", "offers", "solicitation",
    "place", "pop_start", "pop_end", "protest", "url", "is_new", "ja",
]
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
URL_RE = re.compile(r"^https://www\.usaspending\.gov/award/CONT_(AWD|IDV)_")

errors: list[str] = []


def check(cond, msg):
    if not cond:
        errors.append(msg)


def main() -> int:
    try:
        d = json.loads(OUT.read_text())
    except Exception as e:
        print(f"FEED VALIDATION FAILED: {OUT} is not valid JSON ({e})")
        return 1

    for k in ("fiscal_year", "data_through", "summary", "awards"):
        check(k in d, f"top-level key missing: {k}")
    s = d.get("summary", {})
    for k in ("total_awards", "total_obligated", "fy26_obligation_flow",
              "on_award_awards", "on_award_obligated", "on_vehicle_awards", "on_vehicle_obligated",
              "excluded_older_contracts", "new_since_last", "disputed", "with_justification"):
        check(k in s, f"summary key missing: {k}")

    awards = d.get("awards", [])
    check(len(awards) > 0, "feed has zero awards")
    check(s.get("total_awards") == len(awards),
          f"summary.total_awards ({s.get('total_awards')}) != len(awards) ({len(awards)})")
    check(s.get("total_obligated", 0) > 0, "summary.total_obligated is not positive")
    check(bool(DATE_RE.match(str(d.get("data_through")))), f"bad data_through: {d.get('data_through')}")

    disputed = new = with_ja = 0
    on_award_n = on_award_d = on_vehicle_n = on_vehicle_d = 0
    for i, a in enumerate(awards):
        miss = [k for k in REQUIRED if k not in a]
        if miss:
            check(False, f"award {i} ({a.get('piid')}) missing fields: {miss}")
            continue
        check(a["urgency_type"] in ("award", "vehicle"),
              f"award {a['piid']}: bad urgency_type {a['urgency_type']!r}")
        if a["urgency_type"] == "vehicle":
            on_vehicle_n += 1; on_vehicle_d += a["obligated"]
        else:
            on_award_n += 1; on_award_d += a["obligated"]
        if a["ja"]:
            with_ja += 1
            f = JA_DIR / f"{a['piid']}.json"
            check(f.exists(), f"award {a['piid']}: ja=True but {f.name} is missing")
        # awards are filtered to "awarded in FY26", which can include $0 net obligated
        check(isinstance(a["obligated"], (int, float)) and a["obligated"] >= 0,
              f"award {a['piid']}: obligated is negative ({a['obligated']})")
        check(bool(DATE_RE.match(str(a["date"]))), f"award {a['piid']}: bad date {a['date']}")
        check(bool(URL_RE.match(str(a["url"]))), f"award {a['piid']}: bad usaspending url {a['url']}")
        check(bool(str(a["recipient"]).strip()), f"award {a['piid']}: empty recipient")
        if a["protest"]:
            disputed += 1
            check(a["protest"].get("case") and a["protest"].get("outcome"),
                  f"award {a['piid']}: protest missing case/outcome")
        if a["is_new"]:
            new += 1

    check(s.get("on_award_awards") == on_award_n,
          f"summary.on_award_awards ({s.get('on_award_awards')}) != counted ({on_award_n})")
    check(s.get("on_vehicle_awards") == on_vehicle_n,
          f"summary.on_vehicle_awards ({s.get('on_vehicle_awards')}) != counted ({on_vehicle_n})")
    check(on_award_n + on_vehicle_n == len(awards), "urgency_type split doesn't cover all awards")
    # The summary rounds the sum of 2-decimal obligations; the per-award JSON stores
    # each already rounded to whole dollars — so allow sub-dollar-per-award drift.
    check(abs(s.get("on_award_obligated", -1) - on_award_d) <= on_award_n + 1,
          f"summary.on_award_obligated ({s.get('on_award_obligated')}) != counted ({round(on_award_d)})")
    check(abs(s.get("on_vehicle_obligated", -1) - on_vehicle_d) <= on_vehicle_n + 1,
          f"summary.on_vehicle_obligated ({s.get('on_vehicle_obligated')}) != counted ({round(on_vehicle_d)})")
    check(s.get("disputed") == disputed, f"summary.disputed ({s.get('disputed')}) != counted ({disputed})")
    check(s.get("new_since_last") == new, f"summary.new_since_last ({s.get('new_since_last')}) != counted ({new})")
    check(s.get("with_justification") == with_ja,
          f"summary.with_justification ({s.get('with_justification')}) != counted ({with_ja})")

    # The consolidated corpus the site loads for the modal + search must exist and
    # cover exactly the awards flagged ja=True.
    try:
        corpus = json.loads(JA_JSON.read_text())
    except Exception as e:
        corpus = None
        check(False, f"{JA_JSON.name} missing or invalid JSON ({e})")
    if corpus is not None:
        ja_piids = {a["piid"] for a in awards if a.get("ja")}
        check(set(corpus) == ja_piids,
              f"justifications.json keys ({len(corpus)}) != ja=True awards ({len(ja_piids)})")

    if errors:
        print(f"FEED VALIDATION FAILED — {len(errors)} problem(s):")
        for e in errors[:40]:
            print("  -", e)
        return 1
    print(f"feed OK: on the award {on_award_n:,}/${on_award_d/1e9:.2f}B, "
          f"on the vehicle {on_vehicle_n}/${on_vehicle_d/1e9:.2f}B, "
          f"{disputed} disputed, {new} new, data through {d['data_through']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
