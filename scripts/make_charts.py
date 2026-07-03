"""Regenerate the README charts. Produces a TWO-METHOD trend comparison (filtering the raw
urgency code vs. checking the order-level competition field) plus the corrected top-awards
chart. One HuggingFace pass per year; both series computed in a single query.

    python scripts/make_charts.py
      -> urgency_trend.png          (comparison: urgency code alone vs. order-level urgency)
      -> urgency_top_fy2026.png     (largest awards under the corrected method)
"""
import duckdb
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

plt.rcParams["text.parse_math"] = False
RAW = "#c7b9e0"      # light — "urgency code alone"
CORR = "#69539E"     # accent — "order-level urgency" (this tracker)
SERVE = "https://huggingface.co/datasets/abigailhaddad/usaspending-bulk-awards/resolve/main/serve/contracts"
con = duckdb.connect(); con.execute("INSTALL httpfs; LOAD httpfs;")


def yr(fy):
    return f"read_parquet('{SERVE}/{fy}.parquet')"


# Per year, award-level, contracts newly AWARDED that year: dollars two ways —
#   raw       = the urgency code alone (what a naive filter gives)
#   corrected = minus orders whose ORDER-LEVEL basis (fair_opportunity) is non-urgency,
#               i.e. that merely inherited the urgency code from a parent vehicle.
fys, raw, corr = [], [], []
for fy in range(2015, 2027):
    try:
        r, c = con.execute(f"""
          WITH a AS (
            SELECT award_id_piid, sum(federal_action_obligation) oblig,
                   bool_or(modification_number IN ('0','P00000')) awarded,
                   COALESCE(bool_or(fair_opportunity_limited_sources IS NOT NULL
                            AND fair_opportunity_limited_sources NOT ILIKE '%URGEN%'), FALSE) inherited
            FROM {yr(fy)} WHERE other_than_full_and_open_competition ILIKE '%URGENCY%'
            GROUP BY award_id_piid)
          SELECT COALESCE(sum(oblig) FILTER (WHERE awarded), 0),
                 COALESCE(sum(oblig) FILTER (WHERE awarded AND NOT inherited), 0)
          FROM a""").fetchone()
    except Exception as e:
        print(f"  FY{fy}: skipped ({str(e)[:50]})"); continue
    fys.append(fy); raw.append(float(r) / 1e9); corr.append(float(c) / 1e9)
    print(f"  FY{fy}: raw ${raw[-1]:.1f}B  ->  order-level ${corr[-1]:.1f}B", flush=True)

# ── comparison trend ─────────────────────────────────────────────────────────
import numpy as np
x = np.arange(len(fys)); w = 0.4
fig, ax = plt.subplots(figsize=(11.5, 6.2))
fig.subplots_adjust(top=0.78, bottom=0.10, left=0.08, right=0.96)
ax.bar(x - w/2, raw, w, color=RAW, label="All urgency-coded awards (raw FPDS filter)")
ax.bar(x + w/2, corr, w, color=CORR, label="Urgency on the award only (this tracker)")
for i in range(len(fys)):
    if raw[i] - corr[i] > max(raw) * 0.03:       # only label where they visibly differ
        ax.text(x[i] - w/2, raw[i] + max(raw)*0.01, f"${raw[i]:,.0f}B", ha="center", va="bottom", fontsize=7.5, color="#888")
    ax.text(x[i] + w/2, corr[i] + max(raw)*0.01, f"${corr[i]:,.1f}B", ha="center", va="bottom", fontsize=7.5, fontweight="bold", color="#222")
ax.set_xticks(x); ax.set_xticklabels([f"FY{f}" + ("*" if f == 2026 else "") for f in fys], fontsize=10)
ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}B"))
ax.set_ylim(0, max(raw) * 1.15)
ax.spines[["top", "right"]].set_visible(False)
ax.grid(axis="y", color="#ededed", lw=0.8); ax.set_axisbelow(True)
ax.legend(loc="upper left", frameon=False, fontsize=9.5)
fig.text(0.08, 0.955, "Contracting awarded citing urgency — two kinds, counted separately",
         fontsize=14, fontweight="bold", color=CORR, ha="left", va="top")
fig.text(0.08, 0.905,
         "Contracts newly awarded each year. The two bars agree every year but FY2026, where the gap is\n"
         "“urgency on the vehicle” — orders that inherited the code from a parent vehicle set up citing urgency,\n"
         "then competed / sole-sourced at the order level; almost all border wall.  *FY2026 partial.",
         fontsize=9.5, color="#555", ha="left", va="top", linespacing=1.4)
fig.text(0.08, 0.015,
         "Source: USAspending bulk award archive (public domain), mirrored at "
         "huggingface.co/datasets/abigailhaddad/usaspending-bulk-awards.",
         fontsize=7.5, color="#999", ha="left", va="bottom")
fig.savefig("urgency_trend.png", dpi=200, facecolor="white")
print("wrote urgency_trend.png", flush=True)

# ── corrected top FY2026 awards ──────────────────────────────────────────────
KEEP = """WHERE other_than_full_and_open_competition ILIKE '%URGENCY%' GROUP BY award_id_piid
  HAVING NOT COALESCE(bool_or(fair_opportunity_limited_sources IS NOT NULL
                     AND fair_opportunity_limited_sources NOT ILIKE '%URGEN%'), FALSE)
     AND bool_or(modification_number IN ('0','P00000'))"""
top = con.execute(f"""
  WITH a AS (SELECT award_id_piid piid, any_value(recipient_name) rec,
                    sum(federal_action_obligation) oblig FROM {yr(2026)} {KEEP})
  SELECT rec, oblig FROM a ORDER BY oblig DESC LIMIT 12""").fetchall()
labels = [r[:26] for r, o in top][::-1]; vals = [o / 1e6 for r, o in top][::-1]
fig2, ax2 = plt.subplots(figsize=(11, 6.5))
fig2.subplots_adjust(top=0.85, bottom=0.08, left=0.34, right=0.96)
ax2.barh(range(len(vals)), vals, color=CORR)
for i, v in enumerate(vals):
    ax2.text(v + max(vals) * 0.01, i, f"${v:,.0f}M", va="center", fontsize=8.5, color="#222")
ax2.set_yticks(range(len(labels))); ax2.set_yticklabels(labels, fontsize=9)
ax2.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}M"))
ax2.set_xlim(0, max(vals) * 1.12)
ax2.spines[["top", "right"]].set_visible(False)
ax2.grid(axis="x", color="#ededed", lw=0.8); ax2.set_axisbelow(True)
fig2.text(0.02, 0.95, "Largest FY2026 awards where competition was actually skipped, citing urgency",
          fontsize=13, fontweight="bold", color=CORR, ha="left", va="top")
fig2.savefig("urgency_top_fy2026.png", dpi=200, facecolor="white")
print("wrote urgency_top_fy2026.png", flush=True)
