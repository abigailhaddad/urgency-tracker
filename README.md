# urgency-tracker

Track how often the federal government skips competition by invoking **unusual
and compelling urgency** — **FAR 6.302-2**, the exception whose standard is that
the government would suffer *"serious injury, financial or other,"* without it —
and pull the **actual justification** agencies file for it.

Two sources, deliberately split by what's free:

- **Trend (free, no auth):** the [`abigailhaddad/usaspending-bulk-awards`](https://huggingface.co/datasets/abigailhaddad/usaspending-bulk-awards)
  mirror of USAspending, queried directly with DuckDB over `hf://`. FY2015–present.
- **Justification (free key):** the J&A documents agencies must post under FAR
  6.305, pulled from **SAM.gov** (needs a `SAM_API_KEY`).

## Quick start

```bash
pip install -r requirements.txt
pip install notebook            # if you don't already have Jupyter
jupyter notebook demo.ipynb
```

Or open the trend notebook in Colab (no install, no auth):

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/abigailhaddad/urgency-tracker/blob/main/demo.ipynb)

## What it shows

`demo.ipynb` queries the USAspending mirror for the `other_than_full_and_open_competition`
field containing `URGENCY (FAR 6.302-2)` and builds:

- **Urgency usage over time** — actions, dollars, and urgency's share of all
  non-competed dollars, by fiscal year.
- **A trend chart** (`urgency_trend.png`).
- **Who uses it** — top agencies by urgency dollars.
- **The biggest urgency awards** — aggregated to the award level.

![Federal contracting under the urgency exception](urgency_trend.png)

The pattern: a COVID-era spike (FY2021 ~$41B, ~18% of all non-competed dollars),
a quiet FY2022–FY2025 (~$3–5B, ~1.5%), and an elevated **FY2026** — already
~$18B and ~20% of non-competed dollars in a *partial* year.

## The actual justification

USAspending gives you the reason *code*; it doesn't tell you *why*. The J&A
document does. `fetch_justification.py` pulls recent J&A notices from SAM.gov,
extracts the rationale text, and flags the urgency cites:

```bash
SAM_API_KEY=... python fetch_justification.py --days 90 --urgency-only
SAM_API_KEY=... python fetch_justification.py --days 90 --piid 70Z02326C93210002
```

A free `SAM_API_KEY` comes from SAM.gov → Account Details → API Key. SAM.gov is a
separate, keyed source — the trend notebook above needs no key.

## Files

- `demo.ipynb` — the urgency-over-time notebook (free, Colab-able).
- `fetch_justification.py` — pull the actual J&A text from SAM.gov (needs `SAM_API_KEY`).
- `requirements.txt` — `duckdb`, `pandas`, `matplotlib`, `requests`.

## Caveats

- Rows are contract *actions*; dollars are `federal_action_obligation` summed
  (can include de-obligations). An "award" is grouped by `award_id_piid`.
- **FY2026 is partial** (through the latest archive snapshot) — action counts are
  low even where dollars are high.
- The USAspending data is a U.S. Government public-domain work; SAM.gov J&A
  documents are public records (some are redacted).

## License

Code is released into the public domain under [CC0 1.0](LICENSE). The underlying
data are U.S. Government works / public records.
