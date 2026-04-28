"""Publication-quality Plotly template for Red Pitaya notebooks.

import paper_style  # registers the 'paper' template and sets it as default

After import, all subsequent Plotly figures inherit the style. Override per
figure with fig.update_layout(width=..., height=..., template=...) as needed.

Aspect ratio: 25:9 (banner-style, suited to long time-series waveforms).
Colors:        Okabe-Ito colorblind-safe palette.
Typography:    serif (Times New Roman family with cross-platform fallbacks).
Axes:          mirrored (4 sides), inside ticks + minor ticks, no gridlines.
"""
from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio

# ---- 16:9 default canvas --------------------------------------------------
WIDTH = 1280
HEIGHT = 720                      # 1280 * 9 / 16 = 720 (HD)
ASPECT_RATIO = (16, 9)

# ---- Okabe-Ito colorblind-friendly palette --------------------------------
COLORWAY = [
    "#0072B2",  # blue
    "#D55E00",  # vermillion
    "#009E73",  # bluish green
    "#CC79A7",  # reddish purple
    "#E69F00",  # orange
    "#56B4E9",  # sky blue
    "#F0E442",  # yellow
    "#000000",  # black
]

# ---- Shared axis dictionary -----------------------------------------------
_AXIS = dict(
    showline=True,
    linewidth=1.2,
    linecolor="black",
    mirror=True,                # 軸線を上下/左右の 4 辺に
    ticks="inside",             # 主目盛りは内向き
    tickwidth=1.0,
    ticklen=6,
    showgrid=False,
    zeroline=False,
    automargin=True,
    minor=dict(                 # 副目盛りも内向き
        ticks="inside",
        ticklen=3,
        tickwidth=0.8,
        showgrid=False,
    ),
    title=dict(font=dict(size=18)),
    tickfont=dict(size=14),
)

paper_template = go.layout.Template()

paper_template.layout = go.Layout(
    font=dict(
        family="Times New Roman, Times, Liberation Serif, DejaVu Serif, serif",
        size=14,
        color="black",
    ),
    paper_bgcolor="white",
    plot_bgcolor="white",
    autosize=False,         # 重要: Jupyter のセル幅に引き伸ばされず width/height を厳守
    width=WIDTH,
    height=HEIGHT,
    margin=dict(l=80, r=20, t=30, b=60),
    xaxis=_AXIS,
    yaxis=_AXIS,
    legend=dict(
        bgcolor="rgba(255,255,255,0.85)",
        bordercolor="black",
        borderwidth=0.5,
        font=dict(size=12),
        x=1, y=1, xanchor="right", yanchor="top",
    ),
    colorway=COLORWAY,
    title=dict(font=dict(size=16), x=0.02, xanchor="left"),
)

# Default trace styles: thin solid lines for go.Scatter
paper_template.data.scatter = (
    go.Scatter(line=dict(width=1.5)),
)

pio.templates["paper"] = paper_template
pio.templates.default = "paper"


def figsize(rows: int = 1, cols: int = 1, scale: float = 1.0,
            ratio: tuple[int, int] = ASPECT_RATIO) -> tuple[int, int]:
    """Return (width, height) keeping `ratio` per panel.

    For a single plot: figsize() -> (1000, 360).
    For 2 stacked plots: figsize(rows=2) -> (1000, 720).
    For 1x2 side-by-side: figsize(cols=2) -> (1000, 180).

    `scale` multiplies width (height follows from ratio).
    """
    panel_w = WIDTH * scale / cols
    panel_h = panel_w * ratio[1] / ratio[0]
    return int(panel_w * cols), int(panel_h * rows)


def show(fig: go.Figure) -> None:
    """Display fig with responsive=False — guarantees the configured
    width/height are honored (some renderers stretch by default and
    break the 25:9 aspect ratio).
    """
    fig.show(config={"responsive": False})


def export_pdf(fig: go.Figure, path: str, scale: float = 2.0) -> None:
    """Save `fig` as a vector PDF (requires `kaleido`).

    pip install --user kaleido==0.2.1
    """
    fig.write_image(path, format="pdf", scale=scale)


__all__ = [
    "WIDTH", "HEIGHT", "ASPECT_RATIO", "COLORWAY",
    "paper_template", "figsize", "show", "export_pdf",
]
