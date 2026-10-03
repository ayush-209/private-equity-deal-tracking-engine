from src.ai.research import chunk_filing, verify_numbers
from src.ui.formatting import indian_group, inr_cr, mult, pct


def test_indian_grouping():
    assert indian_group(1234567) == "12,34,567"
    assert indian_group(999) == "999"
    assert indian_group(-12345.6, 1) == "-12,345.6"


def test_inr_cr_lakh_crore():
    assert inr_cr(45678) == "₹45,678 cr"
    assert inr_cr(250000) == "₹2.50 lakh cr"
    assert inr_cr(float("nan")) == "n.m."


def test_pct_mult():
    assert pct(0.1234) == "12.3%" and mult(3.456) == "3.5x" and pct(None) == "n.m."


FACTS = [{"id": "M1", "label": "Revenue", "value": "₹12,345 cr", "raw": 12345.0, "source": "test", "as_of": "2026-09-30"},
         {"id": "M2", "label": "EBITDA margin", "value": "18.4%", "raw": 0.184, "source": "test", "as_of": "2026-09-30"}]


def test_verify_accepts_fact_numbers():
    assert verify_numbers("Revenue was ₹12,345 cr [M1] and margin 18.4% [M2] in FY2025.", FACTS) == []


def test_verify_catches_invented_numbers():
    bad = verify_numbers("Margin could reach 25.0% and revenue ₹20,000 cr.", FACTS)
    assert "25.0%" in bad and any("20,000" in b for b in bad)


def test_chunking():
    ch = chunk_filing("word " * 2000, size=1000)
    assert ch[0]["id"] == "F1" and len(ch) == 10


def test_generate_parses_response_and_verifies(monkeypatch):
    import types

    import google.genai as genai

    from src.ai import research

    class FakeModels:
        def generate_content(self, **kw):
            assert "FACT PACK" in kw["contents"]
            return types.SimpleNamespace(text="## Financial Highlights\nRevenue ₹12,345 cr [M1]; margin may hit 30.0%.")

    monkeypatch.setattr(research, "gemini_api_key", lambda: "test")
    monkeypatch.setattr(genai, "Client", lambda api_key: types.SimpleNamespace(models=FakeModels()))
    out = research.generate(FACTS, [])
    assert "Revenue" in out["text"] and out["unverified"] == ["30.0%"]


def test_generate_without_key(monkeypatch):
    from src.ai import research
    monkeypatch.setattr(research, "gemini_api_key", lambda: None)
    assert "error" in research.generate(FACTS, [])
