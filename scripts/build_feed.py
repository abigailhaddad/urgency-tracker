"""build_feed.py — generate site/public/urgent.json for the urgency feed site.

Queries the HF USAspending mirror for the current fiscal year's urgency contracts,
keeps the most recent ones, flags awards that are new since the last monthly drop,
and writes the JSON the static site reads.

Designed to run on a schedule: it rebuilds EVERY run, so the makegov protest data
re-fetches each time (GAO cases are filed/decided continuously, not on the monthly
HF cadence). The output is fully deterministic, so an unchanged week produces a
byte-identical file and the workflow commits nothing / the site doesn't redeploy.
When HF advances OR a protest changes, the file changes and the site redeploys.
The "new since last" flag compares against the piid set captured at the prior drop.

    python scripts/build_feed.py
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys

import duckdb

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from urgency_contracts import HF  # noqa: E402

FY = 2026
OUT = ROOT / "site" / "urgent.json"
STATE = ROOT / "scripts" / "feed_state.json"
# Written J&A text: per-award files (durable cache) + a compact checked-cache, plus a
# single consolidated corpus the site loads once (powers the detail modal AND search).
JA_DIR = ROOT / "site" / "justifications"
JA_STATE = ROOT / "scripts" / "ja_state.json"
JA_JSON = ROOT / "site" / "justifications.json"

# Award-level rollup with the extra fields the detail modal needs (offers,
# solicitation, place/period of performance, NAICS) — things not in the table.
FEED_QUERY = """
SELECT
  award_id_piid                                   AS piid,
  max(recipient_name)                       AS recipient,
  max(awarding_agency_name)                 AS agency,
  max(awarding_sub_agency_name)             AS sub_agency,
  max(funding_agency_name)                  AS funding_agency,
  round(sum(federal_action_obligation), 2)        AS obligated,
  min(action_date)                                AS first_action,
  max(action_date)                                AS last_action,
  max(prime_award_base_transaction_description) AS description,
  max(product_or_service_code_description)  AS category,
  max(award_type)                           AS award_type,
  max(naics_description)                    AS naics,
  max(number_of_offers_received)            AS offers,
  max(solicitation_procedures)              AS solicitation,
  max(primary_place_of_performance_city_name)  AS pop_city,
  max(primary_place_of_performance_state_name) AS pop_state,
  min(period_of_performance_start_date)     AS pop_start,
  max(period_of_performance_current_end_date) AS pop_end,
  max(solicitation_identifier)              AS solicitation_id,
  -- True if the award has a base/new-award action (modification 0) in FY26, i.e.
  -- it was actually *awarded* this year — not just modified or de-obligated.
  bool_or(modification_number IN ('0', 'P00000')) AS awarded_fy26,
  max(parent_award_id_piid)                 AS parent_piid,
  max(contract_award_unique_key)            AS award_key
FROM read_parquet('{src}')
WHERE other_than_full_and_open_competition ILIKE '%URGENCY%'
GROUP BY award_id_piid
-- Keep an award only where its ORDER-LEVEL basis is actually urgency. For a standalone
-- contract or a single-award-vehicle order, fair_opportunity is NULL and the Part-6
-- URGENCY code IS the basis. For an order off a MULTIPLE-award vehicle, the order-level
-- field (fair_opportunity_limited_sources) governs — so drop it unless that field is
-- urgency too. This removes orders that merely INHERITED the URGENCY code from their
-- parent vehicle but were actually competed ("fair opportunity given") or sole-sourced
-- on a different basis ("only one source") — e.g. the big border-wall delivery orders.
-- (COALESCE keeps definitive contracts, whose fair_opportunity is NULL.)
HAVING NOT COALESCE(bool_or(
    fair_opportunity_limited_sources IS NOT NULL
    AND fair_opportunity_limited_sources NOT ILIKE '%URGEN%'), FALSE)
"""

_norm = lambda s: re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def fetch_protests(keys: set) -> dict:
    """Map normalized solicitation/PIID -> GAO protest dict, for FY protests that
    match one of our urgency awards. Optional: needs TANGO_API_KEY (makegov) and the
    tango SDK; returns {} (no protest data) if either is missing or the call fails."""
    if not os.environ.get("TANGO_API_KEY"):
        return {}
    try:
        from tango import TangoClient
        c = TangoClient(api_key=os.environ["TANGO_API_KEY"])
        out, page, pulled = {}, 1, 0
        while True:
            # makegov caps the page size at 100 regardless of the requested limit,
            # so paginate until we've pulled `count` rows (or hit an empty page).
            r = c.list_protests(
                shape="case_number,title,outcome,protester,filed_date,decision_date,solicitation_number",
                filed_date_after=f"{FY - 1}-10-01", limit=100, page=page)
            if not r.results:
                break
            pulled += len(r.results)
            for x in r.results:
                x = dict(x)
                cand = {_norm(x.get("solicitation_number"))}
                cand |= {_norm(t) for t in re.findall(r"\(([^)]+)\)", str(x.get("title") or ""))}
                match = (cand & keys) - {""}
                if match:
                    # GAO has no stable public per-case URL (and pending cases have no
                    # decision page), so we don't link out — just surface what makegov
                    # returned. Uppercase the case number to match GAO's own style.
                    rec = {"case": (x.get("case_number") or "").upper(),
                           "outcome": x.get("outcome") or "Pending",
                           "protester": x.get("protester"),
                           "filed_date": str(x.get("filed_date") or "")[:10],
                           "decision_date": str(x.get("decision_date") or "")[:10]}
                    for k in match:
                        out[k] = rec
            if pulled >= r.count:
                break
            page += 1
        return out
    except Exception as e:  # makegov is best-effort enrichment, never fatal
        print(f"  (protest enrichment skipped: {e})")
        return {}


def build() -> int:
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    df_all = con.execute(FEED_QUERY.format(src=f"{HF}/{FY}.parquet")).df()
    # Total FY26 urgency *obligation flow* — every urgency action, including money
    # added to (or pulled from) contracts awarded in earlier years.
    obligation_flow = float(df_all.obligated.sum())
    # The list is contracts AWARDED in FY26 (a new-award action this year), at any
    # dollar amount incl. $0. Excludes FY26 mods/de-obligations of older contracts.
    df = df_all[df_all.awarded_fy26].copy()
    n_excluded = len(df_all) - len(df)
    data_through = str(df.last_action.max())[:10]

    # Always rebuild — so the makegov protest data re-fetches every run (GAO cases
    # change continuously, not just on the monthly HF drop). The workflow commits
    # only when the output actually changed, so unchanged weeks are still no-ops.
    # "New since the last HF drop" is computed against the piid set captured at the
    # PREVIOUS drop, so the flag stays stable until the next monthly drop lands.
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    cur_piids = set(df.piid)
    if not state:                                    # first run — nothing is "new" yet
        baseline = cur_piids
    elif state.get("data_through") != data_through:  # a new monthly drop landed
        baseline = set(state.get("piids", []))
    else:                                            # same drop — keep the prior baseline
        baseline = set(state.get("prev_piids", []))
    df["url"] = "https://www.usaspending.gov/award/" + df["award_key"].astype(str)
    df["is_new"] = df.piid.isin(cur_piids - baseline)

    # makegov: which of these awards have a GAO bid protest, and how it resolved
    keys = ({_norm(p) for p in df.piid} | {_norm(s) for s in df.solicitation_id}) - {""}
    protests = fetch_protests(keys)

    def clean(v):
        return "" if v is None or str(v).strip().lower() in ("none", "nan") else str(v).strip()

    def place(r):
        c, s = clean(r.pop_city).title(), clean(r.pop_state).title()
        return ", ".join(p for p in (c, s) if p)

    awards = [
        {
            "piid": r.piid,
            "recipient": (r.recipient or "").title(),
            "agency": r.agency or "",                       # top-tier, for the filter
            "sub_agency": r.sub_agency or r.agency,          # for display
            "funding_agency": clean(r.funding_agency),
            "obligated": round(float(r.obligated)),
            "date": str(r.last_action)[:10],
            "description": (r.description or "").strip()[:1000],  # full-ish; table clamps, modal shows all
            "category": clean(r.category).title(),
            "award_type": clean(r.award_type).title(),   # Delivery Order / Definitive Contract / Purchase Order
            "naics": clean(r.naics).title(),
            "offers": (None if clean(r.offers) == "" else int(float(r.offers))),
            "solicitation": clean(r.solicitation).title(),
            "place": place(r),
            "pop_start": str(r.pop_start)[:10] if clean(r.pop_start) else "",
            "pop_end": str(r.pop_end)[:10] if clean(r.pop_end) else "",
            "protest": protests.get(_norm(r.piid)) or protests.get(_norm(r.solicitation_id)),
            "url": r.url,
            "is_new": bool(r.is_new),
            "ja": False,   # set by justification enrichment below (modal lazy-loads the text)
        }
        for r in df.sort_values(["obligated", "piid"], ascending=[False, True]).itertuples()
    ]

    # Written J&A text: for awards that posted a Justification notice to SAM.gov, pull
    # the PDF and stash its text in site/justifications/<piid>.json for the modal. Needs
    # TANGO_API_KEY + the tango SDK + pypdf; if any is missing it's skipped and every
    # award just keeps ja=False (the section simply won't show). Best-effort, never fatal.
    with_ja = 0
    if os.environ.get("TANGO_API_KEY"):
        try:
            import justifications  # noqa: E402  (local module in scripts/)
            sol_by_piid = {p: clean(s) for p, s in zip(df.piid, df.solicitation_id)}
            # Parent vehicle PIID per award — for delivery orders with no J&A of their
            # own, we fall back to the parent IDV's J&A (tagged source='parent').
            parent_by_piid = {p: clean(pp) for p, pp in zip(df.piid, df.parent_piid)}
            # Optional: OCR scanned/image J&As with a vision LLM. Enabled only if an
            # OpenAI key is present; without it, scanned J&As stay 'empty' (link-only).
            ocr_key = os.environ.get("OPENAI_API_KEY")
            with_ja = justifications.enrich(
                awards, sol_by_piid, JA_DIR, JA_STATE, os.environ["TANGO_API_KEY"],
                ocr_api_key=ocr_key,
                ocr_model=os.environ.get("OCR_MODEL", justifications.OCR_MODEL_DEFAULT),
                # Set JA_RECHECK_NONE=0 for a one-time full seed (skip re-querying
                # awards already known to have no J&A). Default on for scheduled CI.
                recheck_none=os.environ.get("JA_RECHECK_NONE", "1") != "0",
                parent_by_piid=parent_by_piid)
        except Exception as e:  # noqa: BLE001
            print(f"  (justification enrichment skipped: {e})")

    # Consolidated corpus (piid -> {text, ocr, sam_url, pdfs}) for every award with a
    # J&A on file. The site fetches this once and uses it for BOTH the modal and
    # full-text search, so the justification text is never bundled into urgent.json.
    corpus = {}
    for a in awards:
        f = JA_DIR / f"{a['piid']}.json"
        if a["ja"] and f.exists():
            r = json.loads(f.read_text())
            corpus[a["piid"]] = {k: r[k] for k in ("text", "ocr", "sam_url", "pdfs", "source", "parent_piid") if k in r}
    JA_JSON.write_text(json.dumps(corpus, indent=1))

    disputed = sum(1 for a in awards if a["protest"])
    feed = {
        "fiscal_year": FY,
        "data_through": data_through,
        "summary": {
            "total_awards": int(len(df)),
            "total_obligated": round(float(df.obligated.sum())),
            "fy26_obligation_flow": round(obligation_flow),
            "excluded_older_contracts": int(n_excluded),
            "new_since_last": int(df.is_new.sum()),
            "disputed": disputed,
            "with_justification": with_ja,
        },
        "awards": awards,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(feed, indent=1))
    STATE.write_text(json.dumps({
        "data_through": data_through,
        "piids": sorted(cur_piids),
        "prev_piids": sorted(baseline),
    }, indent=1))
    print(f"Wrote {OUT} — {len(df):,} awards, ${df.obligated.sum()/1e9:.1f}B, "
          f"{feed['summary']['new_since_last']} new, {disputed} with a GAO protest, "
          f"{with_ja} with a J&A on file (data through {data_through}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(build())
