"""Small shared SVG-building helpers - just string assembly, no dependency
on any drawing/graphics library."""
from xml.sax.saxutils import escape as _escape


def esc(s):
    return _escape(str(s), {'"': "&quot;"})


def svg_doc(width, height, body, extra_defs="", view_box=None):
    vb = view_box or f"0 0 {width} {height}"
    style = (
        "<style>.map-el-label{paint-order:stroke;stroke:#ffffffcc;"
        "stroke-width:3px;stroke-linejoin:round;}</style>"
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="{vb}" font-family="Georgia, serif">'
        f"<defs>{extra_defs}</defs>"
        f"{style}"
        f"{body}"
        f"</svg>"
    )


def rect(x, y, w, h, fill, stroke=None, stroke_width=1, rx=0, opacity=None, extra=""):
    s = f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{fill}" rx="{rx}"'
    if stroke:
        s += f' stroke="{stroke}" stroke-width="{stroke_width}"'
    if opacity is not None:
        s += f' opacity="{opacity}"'
    return s + f" {extra}/>"


def circle(cx, cy, r, fill, stroke=None, stroke_width=1, opacity=None, extra=""):
    s = f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{fill}"'
    if stroke:
        s += f' stroke="{stroke}" stroke-width="{stroke_width}"'
    if opacity is not None:
        s += f' opacity="{opacity}"'
    return s + f" {extra}/>"


def line(x1, y1, x2, y2, stroke, stroke_width=1, dash=None, opacity=None, cap="round"):
    s = (f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
         f'stroke="{stroke}" stroke-width="{stroke_width}" stroke-linecap="{cap}"')
    if dash:
        s += f' stroke-dasharray="{dash}"'
    if opacity is not None:
        s += f' opacity="{opacity}"'
    return s + "/>"


def polyline(points, stroke, stroke_width=1, fill="none", opacity=None):
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    s = f'<polyline points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}" stroke-linejoin="round" stroke-linecap="round"'
    if opacity is not None:
        s += f' opacity="{opacity}"'
    return s + "/>"


def polygon(points, fill, stroke=None, stroke_width=1, opacity=None, extra=""):
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    s = f'<polygon points="{pts}" fill="{fill}"'
    if stroke:
        s += f' stroke="{stroke}" stroke-width="{stroke_width}"'
    if opacity is not None:
        s += f' opacity="{opacity}"'
    return s + f" {extra}/>"


def text(x, y, content, size=12, fill="#000", anchor="start", weight="normal", style="normal", opacity=None, extra=""):
    s = (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" '
         f'text-anchor="{anchor}" font-weight="{weight}" font-style="{style}" {extra}')
    if opacity is not None:
        s += f' opacity="{opacity}"'
    return s + f">{esc(content)}</text>"


def group(body, extra=""):
    return f"<g {extra}>{body}</g>"


# A generically-editable map element: wraps content that the client-side map
# editor can select, drag, recolor, relabel, or delete. `kind` is a
# human-readable category ("room", "block", "hex", "settlement", "feature")
# shown in the editor's side panel; cx/cy is the element's logical center in
# SVG user-space units (used as the drag origin); fill, when given, is the
# element's current color so the editor can offer a color swatch.
def map_el(kind, cx, cy, body, fill=None):
    attrs = f'class="map-el" data-kind="{esc(kind)}" data-cx="{cx:.1f}" data-cy="{cy:.1f}"'
    if fill:
        attrs += f' data-fill="{esc(fill)}"'
    return f"<g {attrs}>{body}</g>"


# Shared "goo" filter for merging overlapping circles into an organic blob
# (used by the cave generator). stdDeviation controls how much the shapes
# melt together before the contrast pass sharpens the edge back up.
def goo_filter(filter_id, std_dev=6):
    return (
        f'<filter id="{filter_id}" x="-20%" y="-20%" width="140%" height="140%">'
        f'<feGaussianBlur in="SourceGraphic" stdDeviation="{std_dev}" result="blur"/>'
        f'<feColorMatrix in="blur" mode="matrix" '
        f'values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 22 -10" result="goo"/>'
        f"</filter>"
    )
