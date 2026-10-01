"""CSV / Excel / JSON exports. PDF exports live in pdf.py."""
from __future__ import annotations

import io
import json

import numpy as np
import pandas as pd


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")  # BOM so Excel renders ₹ correctly


def to_excel_bytes(sheets: dict[str, pd.DataFrame]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for name, df in sheets.items():
            d = df.copy()
            for c in d.columns:
                if d[c].dtype == object:
                    d[c] = d[c].map(lambda v: json.dumps(v, default=str) if isinstance(v, (list, dict)) else v)
            d.replace([np.inf, -np.inf], np.nan).to_excel(xw, sheet_name=name[:31], index=False)
    return buf.getvalue()


def _jsonable(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return None if (np.isnan(o) or np.isinf(o)) else float(o)
    if isinstance(o, pd.DataFrame):
        return json.loads(o.replace([np.inf, -np.inf], np.nan).to_json(orient="records", date_format="iso"))
    if isinstance(o, pd.Series):
        return _jsonable(o.to_dict())
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return o


def to_json_bytes(obj) -> bytes:
    return json.dumps(_jsonable(obj), indent=2, ensure_ascii=False).encode("utf-8")
