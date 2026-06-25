"""urgency_contracts.py — the granular no-bid "urgency" contracts, from USAspending.

Pulls every contract awarded under the "unusual and compelling urgency" exception
(FAR 6.302-2) for a fiscal year, straight from the HuggingFace USAspending mirror
(public domain — no API key, no rate limit), rolls it up to the award level, and
writes a CSV you can open in anything. This is the granular data behind the trend
chart in demo.ipynb.

    python urgency_contracts.py --year 2026
    python urgency_contracts.py --year 2025 --out urgency_2025.csv
"""
from __future__ import annotations

import argparse
import math

import duckdb

HF = "https://huggingface.co/datasets/abigailhaddad/usaspending-bulk-awards/resolve/main/serve/contracts"

# Award-level rollup (group the contract *actions* by PIID) of urgency-coded contracts.
QUERY = """
SELECT
  award_id_piid                                   AS piid,
  any_value(recipient_name)                       AS recipient,
  any_value(recipient_parent_name)                AS parent_recipient,
  any_value(recipient_uei)                         AS recipient_uei,
  any_value(recipient_state_code)                 AS recipient_state,
  any_value(awarding_agency_name)                 AS awarding_agency,
  any_value(awarding_sub_agency_name)             AS awarding_sub_agency,
  any_value(funding_agency_name)                  AS funding_agency,
  round(sum(federal_action_obligation), 2)        AS obligated,
  count(*)                                         AS actions,
  min(action_date)                                AS first_action,
  max(action_date)                                AS last_action,
  any_value(naics_code)                           AS naics,
  any_value(naics_description)                     AS naics_description,
  any_value(product_or_service_code)              AS psc,
  any_value(product_or_service_code_description)  AS psc_description,
  any_value(other_than_full_and_open_competition) AS urgency_reason,
  any_value(transaction_description)              AS description,
  -- fields used to build the USAspending award-page link
  any_value(awarding_sub_agency_code)             AS url_sub,
  any_value(parent_award_id_piid)                 AS url_ppiid,
  any_value(parent_award_agency_id)               AS url_pagency
FROM read_parquet('{src}')
WHERE other_than_full_and_open_competition ILIKE '%URGENCY%'
GROUP BY award_id_piid
ORDER BY obligated DESC
"""


def _missing(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v)) or str(v).strip() in ("", "None", "nan", "<NA>")


def usaspending_url(row) -> str:
    """USAspending award-page URL. The page id is
    CONT_AWD_<piid>_<awarding-subtier>_<parent-piid|-NONE->_<parent-subtier|-NONE->.
    Verified to match the USAspending API across standalone awards, delivery orders,
    and purchase orders."""
    sub = "-NONE-" if _missing(row.url_sub) else row.url_sub
    if _missing(row.url_ppiid):
        tail = "-NONE-_-NONE-"
    else:
        tail = f"{row.url_ppiid}_{'-NONE-' if _missing(row.url_pagency) else row.url_pagency}"
    return f"https://www.usaspending.gov/award/CONT_AWD_{row.piid}_{sub}_{tail}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--year", type=int, default=2026, help="fiscal year (default 2026)")
    ap.add_argument("--out", help="output CSV (default urgency_contracts_fy<year>.csv)")
    args = ap.parse_args()
    out = args.out or f"urgency_contracts_fy{args.year}.csv"

    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    df = con.execute(QUERY.format(src=f"{HF}/{args.year}.parquet")).df()
    df["usaspending_url"] = [usaspending_url(r) for r in df.itertuples()]
    df = df.drop(columns=["url_sub", "url_ppiid", "url_pagency"])
    # surface the link right after the PIID
    df = df[["piid", "usaspending_url"] + [c for c in df.columns if c not in ("piid", "usaspending_url")]]
    df.to_csv(out, index=False)

    print(f"FY{args.year}: {len(df):,} urgency awards, ${df.obligated.sum() / 1e9:,.2f}B obligated -> {out}")
    print("\nTop 10 by dollars:")
    for r in df.head(10).itertuples():
        print(f"  ${r.obligated / 1e6:>10,.1f}M  {str(r.recipient)[:34]:34} {str(r.awarding_sub_agency)[:28]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
