"""AI research layer.

Grounding design
1. A *fact pack* is built deterministically from the database: every fact gets an ID ([M3], [V2], [R1]),
   a source and a date. Optional filing text the user uploads is chunked into [F1], [F2]...
2. The model may only use the fact pack and filing chunks, must cite IDs, and must label interpretation.
3. After generation, every number in the output is checked against the numbers in the fact pack.
   Numbers that cannot be matched are listed in the UI as unverified, rather than trusted.
"""
from __future__ import annotations

import json
import math
import re

import pandas as pd

from src.config import gemini_api_key, gemini_model

SECTIONS = ["Company Overview", "Financial Highlights", "Potential Value-Creation Drivers", "Key Risks",
            "Valuation Observations", "Potential Investment Thesis", "Counter-Thesis", "Due-Diligence Questions",
            "Information Gaps"]

SYSTEM_PROMPT = """You are an analyst on a private equity team writing a preliminary, internal research note.

Rules you must follow:
- Use ONLY the facts in the FACT PACK and the FILING EXCERPTS. Do not use outside knowledge about the company,
  its management, products, customers or news. If something is not in the inputs, write that it is unknown and
  list it under Information Gaps.
- Every sentence that states a figure or a factual claim must end with the fact IDs it relies on, e.g. [M4][V1].
- Never compute new financial figures beyond simple restatement; never invent numbers. Prefer quoting the figures as given.
- Mark analytical judgement explicitly with the prefix "Interpretation:".
- The company may be a synthetic demo company; treat it exactly as described by the facts.
- Write in concise, full sentences. Use the section headings exactly as given, as markdown '## ' headings.
- Present both a thesis and a counter-thesis with equal care. Do not recommend buying or selling.
"""


def _fmt(v, kind):
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return None
    if kind == "text":
        return str(v)
    v = float(v)
    return {"pct": lambda: f"{v*100:.1f}%", "x": lambda: f"{v:.1f}x", "cr": lambda: f"₹{v:,.0f} cr",
            "days": lambda: f"{v:.0f} days", "num": lambda: f"{v:.1f}"}[kind]()


def build_fact_pack(row: pd.Series, hist: pd.DataFrame, flags: list[dict], peer_table: pd.DataFrame,
                    data_source: str, as_of: str) -> list[dict]:
    facts: list[dict] = []

    def add(prefix, label, value, kind, source):
        s = _fmt(value, kind)
        if s is not None:
            facts.append({"id": f"{prefix}{sum(f['id'].startswith(prefix) for f in facts) + 1}",
                          "label": label, "value": s, "raw": None if kind == "text" else float(value),
                          "source": source, "as_of": as_of})

    calc = f"Calculated from {data_source} statements"
    add("C", "Company name", row.company_name, "text", "companies table")
    add("C", "Sector / industry", f"{row.sector} / {row.industry}", "text", "companies table")
    add("C", "Archetype (rules-based)", row.archetype, "text", "Screening engine")
    add("C", "Screening score (0-100)", row.total_score, "num", "Screening engine")
    for _, h in hist.sort_values("fiscal_year").tail(5).iterrows():
        fy = int(h.fiscal_year)
        add("M", f"Revenue FY{fy}", h.revenue, "cr", data_source)
        add("M", f"EBITDA FY{fy}", h.ebitda, "cr", data_source)
        add("M", f"EBITDA margin FY{fy}", h.ebitda_margin, "pct", calc)
        add("M", f"Free cash flow FY{fy}", h.free_cash_flow, "cr", calc)
    for col, label, kind in [("revenue_cagr_3y", "Revenue CAGR 3y", "pct"), ("revenue_cagr_5y", "Revenue CAGR 5y", "pct"),
                             ("ebitda_cagr_3y", "EBITDA CAGR 3y", "pct"), ("roic", "ROIC (latest)", "pct"),
                             ("roe", "ROE (latest)", "pct"), ("fcf_conversion", "FCF conversion (FCF/EBITDA)", "pct"),
                             ("ocf_to_ebitda_3y", "OCF/EBITDA, 3-year cumulative", "pct"),
                             ("net_debt", "Net debt", "cr"), ("net_debt_to_ebitda", "Net debt/EBITDA", "x"),
                             ("interest_coverage", "Interest coverage (EBIT/interest)", "x"),
                             ("cash_conversion_cycle", "Cash conversion cycle", "days"),
                             ("dso", "DSO", "days"), ("ebitda_margin_change_3y", "EBITDA margin change 3y (fraction)", "pct"),
                             ("promoter_holding", "Promoter holding", "pct")]:
        add("M", label, row.get(col), kind, calc if col != "promoter_holding" else data_source)
    add("V", "Market capitalisation", row.get("market_cap"), "cr", data_source)
    add("V", "Enterprise value", row.get("enterprise_value"), "cr", calc)
    for _, p in peer_table.iterrows():
        kind = "pct" if p.multiple == "fcf_yield" else "x"
        add("V", f"{p.multiple} (company)", p.company, kind, calc)
        add("V", f"{p.multiple} peer median ({p.n_peers} peers, {p.peer_group})", p.peer_median, kind, calc)
        add("V", f"{p.multiple} premium(+)/discount(-) to peer median", p.premium_discount, "pct", calc)
    for f in flags:
        facts.append({"id": f"R{sum(x['id'].startswith('R') for x in facts) + 1}", "label": f"Red flag: {f['flag']}",
                      "value": f"{f['explanation']} (current {f['current']}, previous {f['previous']}, "
                               f"threshold {f['threshold']}, severity {f['severity']})",
                      "raw": None, "source": "Red-flag engine", "as_of": as_of})
    return facts


def chunk_filing(text: str, max_chunks: int = 12, size: int = 1800) -> list[dict]:
    text = re.sub(r"\s+", " ", text or "").strip()
    return [{"id": f"F{i+1}", "text": text[i * size:(i + 1) * size]} for i in range(min(max_chunks, math.ceil(len(text) / size)))]


def build_prompt(facts: list[dict], filings: list[dict]) -> str:
    fp = "\n".join(f"[{f['id']}] {f['label']}: {f['value']} (source: {f['source']}, as of {f['as_of']})" for f in facts)
    fx = "\n\n".join(f"[{c['id']}] {c['text']}" for c in filings) or "(none provided)"
    heads = "\n".join(f"## {s}" for s in SECTIONS)
    return (f"FACT PACK\n{fp}\n\nFILING EXCERPTS\n{fx}\n\n"
            f"Write the research note with exactly these sections:\n{heads}\n\n"
            "Under Due-Diligence Questions give 5-8 specific questions tied to cited facts.")


NUM_RE = re.compile(r"(?<![\w\[])[-−]?₹?\s?\d[\d,]*(?:\.\d+)?\s?(?:%|x|cr|days)?", re.I)


def verify_numbers(text: str, facts: list[dict]) -> list[str]:
    """Return numbers in the output that do not match any fact (within rounding)."""
    known = []
    for f in facts:
        for m_ in NUM_RE.finditer(f["value"]):
            v = _num(m_.group())
            if v is not None:
                known.append(v)
        if f.get("raw") is not None:
            known += [f["raw"], f["raw"] * 100]
    clean = re.sub(r"\[[A-Z]\d+\]", "", text)               # drop citations
    clean = re.sub(r"\bFY\s?\d{2,4}\b", "", clean)          # fiscal years
    clean = re.sub(r"^#+.*$", "", clean, flags=re.M)        # headings
    bad = []
    for m_ in NUM_RE.finditer(clean):
        tok = m_.group().strip()
        v = _num(tok)
        if v is None or (abs(v) < 10 and not re.search(r"[%x]|cr|days", tok, re.I)):
            continue  # small bare integers (counts, list numbers) are not financial claims
        if not any(abs(v - k) <= max(0.051, abs(k) * 0.005) for k in known):
            bad.append(tok)
    return sorted(set(bad))


def _num(tok: str):
    t = tok.replace("₹", "").replace(",", "").replace("−", "-").lower()
    t = re.sub(r"(%|x|cr|days)", "", t).strip()
    try:
        return float(t)
    except ValueError:
        return None


def generate(facts: list[dict], filings: list[dict], max_tokens: int = 2500) -> dict:
    key = gemini_api_key()

    if not key:
        return {
            "error": "GEMINI_API_KEY is not set. "
                     "The fact pack below is what the model would receive."
        }

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=key)

    prompt = build_prompt(facts, filings)

    try:
        response = client.models.generate_content(
            model=gemini_model(),
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                max_output_tokens=max_tokens,
            ),
        )

        text = response.text or ""

    except Exception as exc:
        return {"error": f"Gemini API error: {exc}"}

    return {
        "text": text,
        "model": gemini_model(),
        "unverified": verify_numbers(text, facts),
    }


def fact_pack_json(facts: list[dict]) -> dict:
    return json.loads(json.dumps({"facts": facts}, default=str))
