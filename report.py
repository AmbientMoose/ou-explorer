"""Render a downloadable report of the current OU view (parents, unit, children).

Takes a plain, JSON-serializable ``report`` dict (built by the app) and renders
it to three formats:

* ``render_text`` -- a flat, readable text file (URLs as plain text)
* ``render_json`` -- pretty JSON (URLs as string fields)
* ``render_pdf``  -- a formatted PDF with clickable hyperlinks (reportlab)

The report dict shape::

    {
      "app": "IEEE OU Explorer",
      "generated": "2026-07-05 12:00",
      "unit": {spoid, name, type, status, website_url, ou_list_api_url,
               societies[], sections[], regions[], divisions[]},
      "parents":  [{spoid, name, type, supplemented, ou_list_api_url}, ...],
      "children": [ ... same ... ],
      "parents_hidden": int, "children_hidden": int,
      "officers": [{position, name}, ...],
    }
"""

import io
import json
import os
from xml.sax.saxutils import escape

import reportlab
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate

_LINK = "#00629B"          # IEEE blue, matches the app's links
_FONT = "Vera"             # bundled with reportlab; covers Latin + accents

_fonts_ready = False


def _ensure_fonts():
    global _fonts_ready
    if _fonts_ready:
        return
    base = os.path.join(os.path.dirname(reportlab.__file__), "fonts")
    pdfmetrics.registerFont(TTFont("Vera", os.path.join(base, "Vera.ttf")))
    pdfmetrics.registerFont(TTFont("VeraBd", os.path.join(base, "VeraBd.ttf")))
    _fonts_ready = True


# --------------------------------------------------------------------------- #
# JSON
# --------------------------------------------------------------------------- #

def render_json(report):
    return json.dumps(report, indent=2, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# Flat text
# --------------------------------------------------------------------------- #

def _text_unit_line(u):
    bits = [f"{u['name']} ({u['spoid']})", u.get("type", "")]
    return " - ".join(b for b in bits if b)


def render_text(report):
    u = report["unit"]
    out = []
    out.append(f"{report.get('app', 'IEEE OU Explorer')}")
    out.append(f"{u['name']} ({u['spoid']})")
    out.append(f"Generated {report.get('generated', '')}")
    out.append("")

    out.append("SELECTED UNIT")
    line = f"  {u['name']} ({u['spoid']}) - {u.get('type', '')}"
    if u.get("status"):
        line += f" - {u['status']}"
    out.append(line)
    if u.get("url"):
        out.append(f"  Open in OU Explorer: {u['url']}")
    if u.get("website_url"):
        out.append(f"  Website: {u['website_url']}")
    for label, key in (("Societies", "societies"), ("Sections", "sections"),
                       ("Regions", "regions"), ("Divisions", "divisions")):
        if u.get(key):
            out.append(f"  {label}: {', '.join(u[key])}")
    out.append("")

    for title, items, hidden in (
            ("PARENTS", report["parents"], report.get("parents_hidden", 0)),
            ("CHILDREN", report["children"], report.get("children_hidden", 0))):
        out.append(f"{title} ({len(items)})")
        if not items:
            out.append("  None.")
        for it in items:
            out.append(f"  {_text_unit_line(it)}")
            out.append(f"    Open: {it['url']}")
        if hidden:
            out.append(f"  ({hidden} more hidden by the current type filter)")
        out.append("")

    officers = report.get("officers") or []
    out.append(f"OFFICERS ({len(officers)})")
    if not officers:
        out.append("  None available.")
    for o in officers:
        out.append(f"  {o['position']}: {o['name']}")
    out.append("")

    ds = report.get("data_sources") or {}
    out.append("DATA SOURCES")
    if ds.get("ou_list_api"):
        out.append(f"  OU List API: {ds['ou_list_api']}")
    if ds.get("webinabox_unit_details"):
        out.append(f"  WebInABox Unit Details: {ds['webinabox_unit_details']}")
    out.append("")

    return "\n".join(out)


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #

def _link(text, url):
    return (f'<a href="{escape(url, {chr(34): "&quot;"})}" color="{_LINK}">'
            f"<u>{escape(text)}</u></a>")


def _styles():
    return {
        "title": ParagraphStyle("title", fontName="VeraBd", fontSize=15,
                                spaceAfter=2, leading=18),
        "meta": ParagraphStyle("meta", fontName="Vera", fontSize=9,
                               textColor="#555555", spaceAfter=8),
        "h2": ParagraphStyle("h2", fontName="VeraBd", fontSize=12,
                             spaceBefore=10, spaceAfter=4, leading=15),
        "body": ParagraphStyle("body", fontName="Vera", fontSize=10,
                               leading=14),
        "row": ParagraphStyle("row", fontName="Vera", fontSize=10, leading=13,
                              leftIndent=10, spaceAfter=1),
        "note": ParagraphStyle("note", fontName="Vera", fontSize=8.5,
                               textColor="#777777", leftIndent=10,
                               spaceBefore=2, spaceAfter=4),
    }


def _row_paragraph(it, styles):
    label = f"{it['name']} ({it['spoid']})"
    text = _link(label, it["url"])
    tail = escape(it.get("type", ""))
    if tail.strip():
        text += " &ndash; " + tail
    return Paragraph(text, styles["row"])


def render_pdf(report):
    _ensure_fonts()
    styles = _styles()
    u = report["unit"]
    story = []

    story.append(Paragraph(escape(f"{u['name']} ({u['spoid']})"),
                           styles["title"]))
    meta = f"{escape(u.get('type', ''))}"
    if u.get("status"):
        meta += f" &middot; {escape(u['status'])}"
    meta += (f" &middot; {escape(report.get('app', 'IEEE OU Explorer'))}"
             f" &middot; generated {escape(report.get('generated', ''))}")
    story.append(Paragraph(meta, styles["meta"]))

    if u.get("url"):
        story.append(Paragraph("Open in OU Explorer: " + _link(u["url"],
                     u["url"]), styles["body"]))
    if u.get("website_url"):
        story.append(Paragraph("Website: " + _link(u["website_url"],
                     u["website_url"]), styles["body"]))
    for lbl, key in (("Societies", "societies"), ("Sections", "sections"),
                     ("Regions", "regions"), ("Divisions", "divisions")):
        if u.get(key):
            story.append(Paragraph(f"{lbl}: {escape(', '.join(u[key]))}",
                                   styles["body"]))

    for title, items, hidden in (
            ("Parents", report["parents"], report.get("parents_hidden", 0)),
            ("Children", report["children"], report.get("children_hidden", 0))):
        story.append(Paragraph(f"{title} ({len(items)})", styles["h2"]))
        if not items:
            story.append(Paragraph("None.", styles["row"]))
        for it in items:
            story.append(_row_paragraph(it, styles))
        if hidden:
            story.append(Paragraph(
                f"({hidden} more hidden by the current type filter)",
                styles["note"]))

    officers = report.get("officers") or []
    story.append(Paragraph(f"Officers ({len(officers)})", styles["h2"]))
    if not officers:
        story.append(Paragraph("None available.", styles["row"]))
    for o in officers:
        story.append(Paragraph(
            f"{escape(o['position'])}: {escape(o['name'])}", styles["row"]))

    ds = report.get("data_sources") or {}
    story.append(Paragraph("Data Sources", styles["h2"]))
    if ds.get("ou_list_api"):
        story.append(Paragraph("OU List API: " + _link(
            ds["ou_list_api"], ds["ou_list_api"]), styles["body"]))
    if ds.get("webinabox_unit_details"):
        story.append(Paragraph("WebInABox Unit Details: " + _link(
            ds["webinabox_unit_details"], ds["webinabox_unit_details"]),
            styles["body"]))

    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=letter,
                      title=f"{u['name']} ({u['spoid']})",
                      leftMargin=0.8 * inch, rightMargin=0.8 * inch,
                      topMargin=0.7 * inch, bottomMargin=0.7 * inch).build(story)
    return buf.getvalue()
