# urgency-tracker

When the government wants to skip competition on a contract, one of the reasons it's allowed to give is "unusual and compelling urgency" — FAR 6.302-2, where the standard is basically that the government would be seriously injured, financially or otherwise, if it had to take the time to compete the work. I wanted to see how often that actually gets used, and whether I could get at the actual justifications agencies write. This does both.

It's two pieces, split by what costs money to get at.

The trend — how much urgency spending there is over time, which agencies, the biggest awards — comes from my HuggingFace mirror of USAspending. You query it with DuckDB straight over `hf://`, no download and no key. That's `demo.ipynb`, and it opens in Colab.

The actual justification — the J&A document the agency has to post explaining why — comes from SAM.gov, which needs a free API key. That's `fetch_justification.py`. USAspending only gives you the reason *code* (URGENCY); it never tells you why. SAM.gov has the why.

## Running it

```bash
pip install -r requirements.txt
jupyter notebook demo.ipynb        # the trend; also opens in Colab
```

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/abigailhaddad/urgency-tracker/blob/main/demo.ipynb)

For the justifications, get a free key from SAM.gov (Account Details → API Key) and:

```bash
SAM_API_KEY=... python fetch_justification.py --days 90 --urgency-only
SAM_API_KEY=... python fetch_justification.py --days 90 --piid 70Z02326C93210002
```

## Urgency + why + how much, in one table

`urgency_and_why.py` is the one I actually wanted. It pulls recent urgency justifications, grabs the rationale text, joins the dollar figure from the HF mirror on PIID, and flags the bridge/extension ones (the most common pattern). Output is a CSV plus a printed top-by-dollars list.

```bash
SAM_API_KEY=... python urgency_and_why.py --days 120 --year 2026
```

Two honest things about it:

- **SAM's search is rate-limited, so this is built to survive it.** Every notice it processes is cached under `data/cache/`, and `--max-downloads` caps new attachment pulls per run. If SAM cuts you off, it stops, keeps everything cached, and you just re-run to pick up where it left off — nothing is lost.
- **The HF archive runs about two months behind.** So the freshest justifications won't have a dollar match yet — you'll see "(no $ match)" on those, and they fill in once the awards land in the archive. (Also: SAM writes PIIDs with dashes and the mirror strips them, so the join normalizes both.)

## What I found

![Federal contracting under the urgency exception](urgency_trend.png)

There's a big COVID-era spike — FY2021 hit ~$41B, about 18% of all the non-competed contract dollars that year. Then it calms down for a few years (~$3–5B, ~1.5%). And then FY2026, which isn't even a full year yet, is already at ~$18B and ~20% of non-competed dollars — the highest share in the series. I haven't dug into what's driving the FY2026 number, so take it as "worth a look," not a conclusion.

## Caveats (worth reading before you quote a number)

- These are contract *actions* (every modification is a row), and the dollars are obligations summed up — which can include de-obligations. I group by `award_id_piid` when I want award-level.
- FY2026 is partial — only through whatever the latest archive snapshot is. The action counts are low because of that, even where the dollars are high.
- "Urgency" here means the `other_than_full_and_open_competition` field says `URGENCY (FAR 6.302-2)`. That's the agency's own coding.
- The J&A documents are public records, but a lot of them are partly redacted, so the rationale text you get back is sometimes thin.

## Sources

USAspending bulk award data (public domain, U.S. Government), mirrored at [`abigailhaddad/usaspending-bulk-awards`](https://huggingface.co/datasets/abigailhaddad/usaspending-bulk-awards). J&A documents from SAM.gov. Code's CC0 — do whatever you want with it.
