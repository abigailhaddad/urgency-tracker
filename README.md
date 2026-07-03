# urgency-tracker

When the government wants to skip competition on a contract, one of the reasons it's allowed to give is "unusual and compelling urgency" — FAR 6.302-2, where the standard is basically that the government would be seriously injured, financially or otherwise, if it had to take the time to compete the work. I wanted to see how often that actually gets used, and where the money goes.

**Live site: [urgency-tracker.vercel.app](https://urgency-tracker.vercel.app)** — a searchable, filterable table of every urgency award (click a row for detail), with GAO bid-protest status pulled from makegov. It rebuilds when the data updates (see `site/` and `scripts/build_feed.py`).

It all runs off my HuggingFace mirror of USAspending — you query it with DuckDB straight over `hf://`, **no download, no API key, no rate limit**. `demo.ipynb` is the whole thing, and it opens in Colab.

## What I found

There are **two kinds** of urgency award, and the site and this chart report them **separately — never summed.**

![Federal contracting under the urgency exception, counted two ways](urgency_trend.png)

**Why two bars.** The FPDS competition code `other_than_full_and_open_competition = URGENCY (FAR 6.302-2)` has a wrinkle: a task or delivery order *inherits* it from the parent contract vehicle it's placed against. So the same code means two different things:

- **Urgency on the award** (dark bars, ~$3.8B in FY2026): urgency is the basis on this award itself — a standalone sole-source contract, or an order whose own competition basis (`fair_opportunity_limited_sources`, FAR 16.505) is urgency. Competition was skipped *here*, for urgency.
- **Urgency on the vehicle** (the gap up to the light bars, ~$23B in FY2026): the award only inherited the urgency code from a parent vehicle set up citing urgency. At the *order* level it was **competed** among the vehicle's awardees ("fair opportunity given") or sole-sourced on a **different** ground ("only one source"). Almost all of it is border-wall construction.

For eleven of the twelve years the two bars are identical — most urgency awards are standalone sole-source contracts, with no parent code to inherit. They diverge only in **FY2026**, where the border-wall delivery orders push the raw code total to ~$27B while urgency-on-the-award stays at ~$3.8B. The live site labels each award and lets you filter to either kind; `urgency_contracts.py` writes both to the CSV with an `urgency_basis` column.

## The granular data

`urgency_contracts.py` pulls every urgency contract for a year into a CSV — recipient, agency, dollars, dates, NAICS/PSC, description, an `urgency_basis` column (`on the award` vs `on the vehicle (inherited)`) with the `order_level_competition` field behind it, and a **direct USAspending link** (`usaspending_url`) to each award's page, so you can click straight through to verify it:

```bash
python urgency_contracts.py --year 2026
```

On each award page, the urgency designation is under **Additional Details → Competition Details**, where *"Other than Full and Open Competition"* reads **"Urgency."** (That's the reason *code*, not the written justification — the page tells you it was urgency, not why.)

The repo ships `urgency_contracts_fy2026.csv` (2,755 awards) so you can just open it without running anything.

## Running the notebook

```bash
pip install -r requirements.txt
jupyter notebook demo.ipynb        # also opens in Colab — no key needed
```

## Why the mirror, and not the USAspending API

USAspending's search API has **no working filter for the urgency reason** — I checked: it accepts the filter and silently ignores it (you get all 2.9M-odd contracts back either way). To pull the urgency subset through the API you'd have to page through awards and make a per-award detail call on each one to read the competition field, and the trend (aggregate dollars per year) isn't really doable at all. On the mirror it's one line —

```sql
WHERE other_than_full_and_open_competition ILIKE '%URGENCY%'
```

— filtering *and* aggregating twenty years at once.

## Caveats (worth reading before you quote a number)

- These are contract *actions* (every modification is a row); dollars are obligations summed, which can include de-obligations. I group by `award_id_piid` for award-level.
- FY2026 is partial — through the latest archive snapshot — so action counts are low even where dollars are high.
- "Urgency" = the `other_than_full_and_open_competition` field saying `URGENCY (FAR 6.302-2)`. That's the agency's own coding, and USAspending records the reason *code*, not the written justification.
- Competition coding is mandatory FPDS reporting, so this is the complete universe, not a sample.

## Sources

USAspending bulk award data (public domain, U.S. Government), mirrored at [`abigailhaddad/usaspending-bulk-awards`](https://huggingface.co/datasets/abigailhaddad/usaspending-bulk-awards). Code's CC0 — do whatever you want with it.
