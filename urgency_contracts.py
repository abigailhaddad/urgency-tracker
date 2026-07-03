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
  any_value(fair_opportunity_limited_sources)     AS order_level_competition,
  -- Two kinds, by WHERE urgency was invoked. 'on the award' = urgency is the basis on
  -- this award itself (standalone contract, or order whose own fair-opportunity basis is
  -- urgency). 'on the vehicle (inherited)' = the award only inherited the URGENCY code
  -- from a parent vehicle set up citing urgency; at the ORDER level it was competed
  -- ("fair opportunity given") or sole-sourced on another basis ("only one source").
  -- These are reported SEPARATELY, never summed — same split as the live site.
  CASE WHEN COALESCE(bool_or(
        fair_opportunity_limited_sources IS NOT NULL
        AND fair_opportunity_limited_sources NOT ILIKE '%URGEN%'), FALSE)
       THEN 'on the vehicle (inherited)' ELSE 'on the award' END AS urgency_basis,
  any_value(transaction_description)              AS description,
  -- USAspending's own canonical award key (handles awards vs IDVs correctly)
  any_value(contract_award_unique_key)            AS award_key
FROM read_parquet('{src}')
WHERE other_than_full_and_open_competition ILIKE '%URGENCY%'
GROUP BY award_id_piid
ORDER BY obligated DESC
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--year", type=int, default=2026, help="fiscal year (default 2026)")
    ap.add_argument("--out", help="output CSV (default urgency_contracts_fy<year>.csv)")
    args = ap.parse_args()
    out = args.out or f"urgency_contracts_fy{args.year}.csv"

    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    df = con.execute(QUERY.format(src=f"{HF}/{args.year}.parquet")).df()
    # USAspending's award page is /award/<contract_award_unique_key> (CONT_AWD_… or CONT_IDV_…).
    df["usaspending_url"] = "https://www.usaspending.gov/award/" + df["award_key"].astype(str)
    df = df.drop(columns=["award_key"])
    # surface the link right after the PIID
    df = df[["piid", "usaspending_url"] + [c for c in df.columns if c not in ("piid", "usaspending_url")]]
    df.to_csv(out, index=False)

    on_award = df[df.urgency_basis == "on the award"]
    on_vehicle = df[df.urgency_basis != "on the award"]
    print(f"FY{args.year}: {len(df):,} urgency awards -> {out}  (two kinds, counted separately)")
    print(f"  urgency on the award:   {len(on_award):,} awards, ${on_award.obligated.sum() / 1e9:,.2f}B")
    print(f"  urgency on the vehicle: {len(on_vehicle):,} awards, ${on_vehicle.obligated.sum() / 1e9:,.2f}B  (inherited from parent vehicle; competed/other at the order level)")
    print("\nTop 10 by dollars:")
    for r in df.head(10).itertuples():
        print(f"  ${r.obligated / 1e6:>10,.1f}M  {str(r.recipient)[:34]:34} {str(r.awarding_sub_agency)[:28]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
