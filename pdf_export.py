"""Render tutor-generated Markdown into an actual PDF.

Handles the constructs the tutor's own prompts ask it to produce — headers,
bold/italic/inline code, bullet and numbered lists, GFM pipe tables, and fenced
code blocks — so saved cheat sheets/study guides show real formatted tables
instead of raw markdown syntax.
"""
from __future__ import annotations

import io
import re

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    ListFlowable,
    ListItem,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

_STYLES = getSampleStyleSheet()

_BODY = ParagraphStyle("TutorBody", parent=_STYLES["BodyText"], fontSize=10, leading=14, spaceAfter=6)
_H1 = ParagraphStyle("TutorH1", parent=_STYLES["Heading1"], fontSize=18, spaceBefore=6, spaceAfter=10)
_H2 = ParagraphStyle("TutorH2", parent=_STYLES["Heading2"], fontSize=14, spaceBefore=12, spaceAfter=6)
_H3 = ParagraphStyle("TutorH3", parent=_STYLES["Heading3"], fontSize=12, spaceBefore=10, spaceAfter=4)
_QUOTE = ParagraphStyle("TutorQuote", parent=_BODY, leftIndent=14, textColor=colors.HexColor("#555555"))
_CELL = ParagraphStyle("TutorCell", parent=_STYLES["BodyText"], fontSize=9, leading=12)
_CELL_HEAD = ParagraphStyle("TutorCellHead", parent=_CELL, fontName="Helvetica-Bold")
_CODE = ParagraphStyle("TutorCode", parent=_STYLES["Code"], fontSize=8.5, leading=11)

_CODE_RE = re.compile(r"`([^`]+?)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
# Triple markers (***bold italic***) must be handled before the double/single
# markers below — otherwise a greedy/lazy double-star match eats only 2 of the
# 3 stars on each side, leaving a stray star that the italic pass then pairs
# with one from *outside* the just-inserted <b> tag, producing invalid
# overlapping markup like <b><i>text</b></i> that crashes reportlab's parser.
_BOLD_ITALIC_STAR_RE = re.compile(r"\*\*\*(.+?)\*\*\*")
_BOLD_ITALIC_US_RE = re.compile(r"___(.+?)___")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_STAR_RE = re.compile(r"\*(.+?)\*")
_ITALIC_US_RE = re.compile(r"(?<!\w)_(.+?)_(?!\w)")


def _inline(text: str) -> str:
    """Escape XML, then translate **bold**, *italic*, `code`, and [text](url) links."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # Protect inline code first so bold/italic markers inside it (e.g. `sum_first_n`)
    # are never misread as formatting.
    codes: list[str] = []

    def _stash(m: re.Match) -> str:
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"

    text = _CODE_RE.sub(_stash, text)
    text = _LINK_RE.sub(r'<link href="\2" color="blue">\1</link>', text)
    text = _BOLD_ITALIC_STAR_RE.sub(r"<b><i>\1</i></b>", text)
    text = _BOLD_ITALIC_US_RE.sub(r"<b><i>\1</i></b>", text)
    text = _BOLD_RE.sub(r"<b>\1</b>", text)
    text = _ITALIC_STAR_RE.sub(r"<i>\1</i>", text)
    text = _ITALIC_US_RE.sub(r"<i>\1</i>", text)

    for idx, code in enumerate(codes):
        text = text.replace(f"\x00{idx}\x00", f'<font face="Courier">{code}</font>')
    return text


def _safe_paragraph(text: str, style: ParagraphStyle) -> Paragraph:
    """Build a Paragraph, falling back to unformatted plain text if the markup
    reportlab receives is ever malformed (unbalanced/overlapping tags from
    unanticipated markdown shapes). One bad line must never crash the whole PDF.
    """
    try:
        return Paragraph(_inline(text), style)
    except Exception:
        plain = (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return Paragraph(plain, style)


def _is_table_row(line: str) -> bool:
    return line.startswith("|") and line.endswith("|") and len(line) > 1


def _is_separator_row(line: str) -> bool:
    return bool(re.fullmatch(r"\|?[\s:|-]+\|?", line.strip())) and "-" in line


def _split_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [cell.strip() for cell in line.split("|")]


def _table_flowable(rows: list[list[str]], avail_width: float) -> Table:
    num_cols = max(len(r) for r in rows)
    data = []
    for i, row in enumerate(rows):
        cells = row + [""] * (num_cols - len(row))
        style = _CELL_HEAD if i == 0 else _CELL
        data.append([_safe_paragraph(c, style) for c in cells])
    col_width = avail_width / num_cols
    table = Table(data, colWidths=[col_width] * num_cols, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#999999")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF7")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _flowables(markdown_text: str, avail_width: float) -> list:
    lines = (markdown_text or "").replace("\r\n", "\n").split("\n")
    flow: list = []
    i, n = 0, len(lines)
    paragraph_buf: list[str] = []

    def flush_paragraph() -> None:
        if paragraph_buf:
            text = " ".join(part for part in paragraph_buf if part)
            if text:
                flow.append(_safe_paragraph(text, _BODY))
            paragraph_buf.clear()

    while i < n:
        stripped = lines[i].strip()

        if not stripped:
            flush_paragraph()
            i += 1
            continue

        if stripped.startswith("```"):
            flush_paragraph()
            i += 1
            code_lines = []
            while i < n and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1  # skip closing fence
            flow.append(Preformatted("\n".join(code_lines), _CODE))
            flow.append(Spacer(1, 6))
            continue

        m = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if m:
            flush_paragraph()
            style = {1: _H1, 2: _H2}.get(len(m.group(1)), _H3)
            flow.append(_safe_paragraph(m.group(2), style))
            i += 1
            continue

        if re.fullmatch(r"-{3,}|\*{3,}|_{3,}", stripped):
            flush_paragraph()
            flow.append(Spacer(1, 4))
            i += 1
            continue

        if _is_table_row(stripped) and i + 1 < n and _is_separator_row(lines[i + 1].strip()):
            flush_paragraph()
            rows = [_split_row(stripped)]
            i += 2  # header + separator
            while i < n and _is_table_row(lines[i].strip()):
                rows.append(_split_row(lines[i].strip()))
                i += 1
            flow.append(_table_flowable(rows, avail_width))
            flow.append(Spacer(1, 8))
            continue

        if re.match(r"^[-*+]\s+", stripped):
            flush_paragraph()
            items = []
            while i < n and re.match(r"^[-*+]\s+", lines[i].strip()):
                item_text = re.sub(r"^[-*+]\s+", "", lines[i].strip())
                items.append(ListItem(_safe_paragraph(item_text, _BODY)))
                i += 1
            flow.append(ListFlowable(items, bulletType="bullet", leftIndent=16))
            flow.append(Spacer(1, 6))
            continue

        if re.match(r"^\d+\.\s+", stripped):
            flush_paragraph()
            items = []
            while i < n and re.match(r"^\d+\.\s+", lines[i].strip()):
                item_text = re.sub(r"^\d+\.\s+", "", lines[i].strip())
                items.append(ListItem(_safe_paragraph(item_text, _BODY)))
                i += 1
            flow.append(ListFlowable(items, bulletType="1", leftIndent=16))
            flow.append(Spacer(1, 6))
            continue

        if stripped.startswith(">"):
            flush_paragraph()
            quote_lines = []
            while i < n and lines[i].strip().startswith(">"):
                quote_lines.append(re.sub(r"^>\s?", "", lines[i].strip()))
                i += 1
            flow.append(_safe_paragraph(" ".join(quote_lines), _QUOTE))
            continue

        paragraph_buf.append(stripped)
        i += 1

    flush_paragraph()
    return flow


def markdown_to_pdf(markdown_text: str, pdf_title: str = "") -> bytes:
    """Render a self-contained Markdown document (its own leading H1 included) as PDF bytes.

    `pdf_title` only sets the PDF's internal document-properties title, not any
    visible heading — the visible title comes from the markdown's own `# ...` line.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=LETTER,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        title=pdf_title or "Tutor Document",
    )
    story = _flowables(markdown_text, doc.width)
    if not story:
        story = [Paragraph(" ", _BODY)]
    doc.build(story)
    return buf.getvalue()
