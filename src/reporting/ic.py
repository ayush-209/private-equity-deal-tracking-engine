"""Investment-committee one-pager: assembled deterministically from engine outputs (no LLM figures)."""
from __future__ import annotations

import datetime as dt

import pandas as pd
from fpdf import FPDF

from src.config import ROOT
from src.risk.red_flags import SEVERITY_ORDER, dd_questions
from src.screening.engine import DIM_LABELS, DIMENSIONS
from src.ui.formatting import inr_cr, mult, pct, pp
from src.valuation.peers import explain_valuation, peer_stats

FONT_DIR = ROOT / "assets" / "fonts"


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def value_drivers(r: pd.Series) -> list[str]:
    out = [f"{r.archetype}: " + "; ".join(r.archetype_reasons)] if r.archetype != "Further Investigation" else []
    dims = sorted([(r[f"{d}_score"] / r[f"{d}_max"], d) for d in DIMENSIONS if pd.notna(r[f"{d}_score"])], reverse=True)
    for share, d in dims[:3]:
        if share >= 0.6:
            out.append(f"{DIM_LABELS[d]} averages the {_ordinal(round(share * 100))} percentile of its peer group.")
    if pd.notna(r.get("ebitda_margin_change_3y")) and r.ebitda_margin_change_3y > 0.02:
        out.append(f"EBITDA margin up {pp(r.ebitda_margin_change_3y)} over 3 years — operating leverage to test.")
    if r.get("leverage_class") == "Low" and pd.notna(r.get("fcf_conversion")) and r.fcf_conversion > 0.5:
        out.append("Unlevered balance sheet with cash conversion that could support acquisition debt.")
    return out[:3] or ["No dimension ranks strongly versus peers; value creation would rely on operational change."]


def ic_summary(uni, company_id: int, lbo: dict | None = None) -> dict:
    t = uni.table.set_index("company_id")
    r = t.loc[company_id].copy()
    r["company_id"] = company_id
    flags = sorted(uni.flags.get(company_id, []), key=lambda f: SEVERITY_ORDER.get(f["severity"], 3))
    ps = peer_stats(uni.table, company_id).set_index("multiple")
    qs = dd_questions(flags, r.ev_ebitda_premium, r.data_quality, r)
    return {
        "company": r.company_name, "ticker": r.ticker, "sector": f"{r.sector} / {r.industry}",
        "score": r.total_score, "archetype": r.archetype, "ev": r.enterprise_value, "market_cap": r.market_cap,
        "data_quality": r.data_quality, "passes": bool(r.passes_screen),
        "profile": {"Revenue CAGR (3y)": pct(r.revenue_cagr_3y), "EBITDA margin": pct(r.ebitda_margin),
                    "ROIC": pct(r.roic), "FCF conversion": pct(r.fcf_conversion),
                    "Net debt / EBITDA": "Net cash" if pd.notna(r.net_debt) and r.net_debt <= 0 else mult(r.net_debt_to_ebitda),
                    "Leverage class": r.leverage_class},
        "valuation": {"EV/EBITDA": mult(r.ev_ebitda), "Peer median": mult(ps.loc["ev_ebitda", "peer_median"]),
                      "Premium / discount": pct(r.ev_ebitda_premium, signed=True),
                      "Peers": f"{int(ps.loc['ev_ebitda', 'n_peers'])} ({r.peer_group.split(':')[0]})"},
        "scores": {DIM_LABELS[d]: f"{r[f'{d}_score']:.1f} / {r[f'{d}_max']:.0f}" if pd.notna(r[f"{d}_score"]) else "n/a"
                   for d in DIMENSIONS},
        "drivers": value_drivers(r),
        "risks": [f"{f['flag']} — {f['explanation']}" for f in flags[:3]] or ["No automated red flags triggered."],
        "valuation_notes": explain_valuation(uni.table, company_id),
        "dd": [q["question"] for q in qs[:3]],
        "lbo": lbo,
        "sources": ", ".join(uni.sources), "synthetic": uni.is_synthetic,
        "generated": dt.datetime.now().strftime("%d %b %Y %H:%M"),
    }


class _PDF(FPDF):
    def __init__(self):
        super().__init__(format="A4")
        self.add_font("DejaVu", "", str(FONT_DIR / "DejaVuSans.ttf"))
        self.add_font("DejaVu", "B", str(FONT_DIR / "DejaVuSans-Bold.ttf"))
        self.set_auto_page_break(True, 14)
        self.set_margins(14, 14, 14)


def _section(pdf, title):
    pdf.ln(2)
    pdf.set_font("DejaVu", "B", 10)
    pdf.set_text_color(20, 52, 84)
    pdf.cell(0, 6, title, new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(20, 52, 84)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(1.5)
    pdf.set_text_color(30, 30, 30)
    pdf.set_font("DejaVu", "", 8.8)


def _kv(pdf, items: dict, cols: int = 2):
    w = (pdf.w - pdf.l_margin - pdf.r_margin) / cols
    keys = list(items)
    for i in range(0, len(keys), cols):
        for k in keys[i:i + cols]:
            pdf.set_font("DejaVu", "", 8.8)
            pdf.cell(w * 0.55, 5, k)
            pdf.set_font("DejaVu", "B", 8.8)
            pdf.cell(w * 0.45, 5, str(items[k]))
        pdf.ln(5)


def _numbered(pdf, lines):
    pdf.set_font("DejaVu", "", 8.8)
    for i, s in enumerate(lines, 1):
        pdf.multi_cell(0, 4.6, f"{i}. {s}", new_x="LMARGIN", new_y="NEXT")


def ic_pdf(s: dict) -> bytes:
    pdf = _PDF()
    pdf.add_page()
    if s["synthetic"]:
        pdf.set_fill_color(255, 236, 200)
        pdf.set_font("DejaVu", "B", 8.5)
        pdf.cell(0, 6, "SYNTHETIC DEMO DATA — fictitious company, not investment material", fill=True,
                 new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.ln(1)
    pdf.set_font("DejaVu", "B", 16)
    pdf.cell(0, 9, s["company"], new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", "", 9)
    pdf.set_text_color(90, 90, 90)
    pdf.multi_cell(0, 4.6, f"{s['ticker']}  |  {s['sector']}\nPreliminary screening summary — for discussion only",
                   new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(30, 30, 30)
    _section(pdf, "Screening result")
    _kv(pdf, {"Screening score": f"{s['score']:.0f} / 100", "Archetype": s["archetype"],
              "Enterprise value": inr_cr(s["ev"]), "Market cap": inr_cr(s["market_cap"]),
              "Data quality": s["data_quality"], "Passes screen": "Yes" if s["passes"] else "No"})
    _section(pdf, "Score breakdown")
    _kv(pdf, s["scores"], cols=3)
    _section(pdf, "Financial profile")
    _kv(pdf, s["profile"])
    _section(pdf, "Valuation")
    _kv(pdf, s["valuation"])
    pdf.set_font("DejaVu", "", 8.8)
    for n in s["valuation_notes"]:
        pdf.multi_cell(0, 4.6, f"• {n}", new_x="LMARGIN", new_y="NEXT")
    if s.get("lbo") and "error" not in s["lbo"]:
        l = s["lbo"]
        _section(pdf, "Indicative deal economics (illustrative control case)")
        _kv(pdf, {"Entry EV": inr_cr(l["entry_ev"]), "Sponsor equity": inr_cr(l["sponsor_equity"]),
                  "Exit EV": inr_cr(l["exit_ev"]), "Exit equity": inr_cr(l["exit_equity"]),
                  "MOIC": mult(l["moic"], 2), "IRR": pct(l["irr"])})
    _section(pdf, "Potential value drivers")
    _numbered(pdf, s["drivers"])
    _section(pdf, "Key risks")
    _numbered(pdf, s["risks"])
    _section(pdf, "Due-diligence priorities")
    _numbered(pdf, s["dd"])
    pdf.ln(3)
    pdf.set_font("DejaVu", "", 7)
    pdf.set_text_color(110, 110, 110)
    pdf.multi_cell(0, 3.6, f"Sources: {s['sources']}. Generated {s['generated']}. All figures in ₹ crore, calculated "
                           "deterministically from the stored statements; scores are percentiles within peer groups. "
                           "Not investment advice.", new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def deep_dive_pdf(s: dict, history: pd.DataFrame, flags: list[dict]) -> bytes:
    """IC page plus a financial history table and the full red-flag list."""
    pdf = _PDF()
    pdf.add_page()
    pdf.set_font("DejaVu", "B", 14)
    pdf.cell(0, 8, f"{s['company']} — company deep dive", new_x="LMARGIN", new_y="NEXT")
    _section(pdf, "Financial history (₹ cr)")
    cols = [("fiscal_year", "FY", lambda v: f"FY{int(v)}"), ("revenue", "Revenue", lambda v: inr_cr(v).replace("₹", "").replace(" cr", "")),
            ("ebitda", "EBITDA", lambda v: inr_cr(v).replace("₹", "").replace(" cr", "")),
            ("ebitda_margin", "Margin", pct), ("free_cash_flow", "FCF", lambda v: inr_cr(v).replace("₹", "").replace(" cr", "")),
            ("roic", "ROIC", pct), ("net_debt_to_ebitda", "ND/EBITDA", mult), ("cash_conversion_cycle", "CCC", lambda v: f"{v:.0f}" if pd.notna(v) else "n.m.")]
    w = (pdf.w - 28) / len(cols)
    pdf.set_font("DejaVu", "B", 8)
    for _, lab, _f in cols:
        pdf.cell(w, 5, lab, border="B")
    pdf.ln()
    pdf.set_font("DejaVu", "", 8)
    for _, h in history.sort_values("fiscal_year").iterrows():
        for c, _l, f in cols:
            pdf.cell(w, 5, f(h[c]))
        pdf.ln()
    _section(pdf, "All red flags")
    if not flags:
        pdf.multi_cell(0, 4.6, "None triggered.", new_x="LMARGIN", new_y="NEXT")
    for f in flags:
        pdf.multi_cell(0, 4.6, f"[{f['severity']}] {f['category']} — {f['flag']}: {f['explanation']} "
                               f"(current {f['current']}, previous {f['previous']}, threshold {f['threshold']})",
                       new_x="LMARGIN", new_y="NEXT")
    # merge: IC page first, then the detail pages
    from pypdf import PdfReader, PdfWriter
    import io
    out = PdfWriter()
    for src in (ic_pdf(s), bytes(pdf.output())):
        for p in PdfReader(io.BytesIO(src)).pages:
            out.add_page(p)
    buf = io.BytesIO()
    out.write(buf)
    return buf.getvalue()
