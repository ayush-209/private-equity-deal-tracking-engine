"""Plotly styling shared by all pages."""
import plotly.graph_objects as go
import plotly.io as pio

TEAL, OCHRE, SLATE, RED, GREEN, SAND = "#0E5C63", "#B7862B", "#5B6770", "#B23A3A", "#2E7D4F", "#D9CBA3"
COLORWAY = [TEAL, OCHRE, SLATE, "#7A9E9F", "#8C5E3C", "#3E7CB1", GREEN, RED]

pio.templates["pe"] = go.layout.Template(layout=go.Layout(
    colorway=COLORWAY, font=dict(family="Inter, Segoe UI, Roboto, sans-serif", size=12, color="#1B2A33"),
    paper_bgcolor="white", plot_bgcolor="white", margin=dict(l=10, r=10, t=40, b=10),
    xaxis=dict(gridcolor="#E7ECEC", zerolinecolor="#C9D2D2"), yaxis=dict(gridcolor="#E7ECEC", zerolinecolor="#C9D2D2"),
    hoverlabel=dict(bgcolor="white"), title=dict(font=dict(size=14))))
pio.templates.default = "pe"

ARCHETYPE_COLORS = {"Compounder": TEAL, "Cash Flow Compounder": "#3E7CB1", "Margin Expansion": OCHRE,
                    "Turnaround Candidate": "#8C5E3C", "Value Opportunity": GREEN, "Leveraged Opportunity": "#7A9E9F",
                    "Further Investigation": "#B9C2C2"}
