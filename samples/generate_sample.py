"""Generate samples/workplace-fire-safety-module-1.pdf, the sample course module used by the demo.

The text was written for this demo. It is a short, generic training module, not safety guidance.

    pip install -r samples/requirements.txt
    python samples/generate_sample.py

The module has numbered headings on two levels, bold runs, bullet and numbered lists, two tables
(the first one continues on the next page), two figures with captions and two "Note" / "Remember"
boxes, so it exercises every rule of the bitmark writer.
"""

import io
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image as PdfImage,
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

HERE = Path(__file__).resolve().parent
OUT = HERE / "workplace-fire-safety-module-1.pdf"

styles = getSampleStyleSheet()
BODY = ParagraphStyle("body", parent=styles["Normal"], fontName="Helvetica", fontSize=10.5, leading=14.5,
                      spaceAfter=6)
CELL = ParagraphStyle("cell", parent=BODY, fontSize=9.5, leading=12, spaceAfter=0)
CELL_HEAD = ParagraphStyle("cellhead", parent=CELL, fontName="Helvetica-Bold")
H1 = ParagraphStyle("h1", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=16, spaceBefore=10,
                    spaceAfter=6)
H2 = ParagraphStyle("h2", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=12.5, spaceBefore=8,
                    spaceAfter=4)
TITLE = ParagraphStyle("title", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=22, alignment=0,
                       spaceAfter=4)
SUBTITLE = ParagraphStyle("subtitle", parent=BODY, fontName="Helvetica-Oblique", fontSize=12, spaceAfter=12)
CAPTION = ParagraphStyle("caption", parent=BODY, fontName="Helvetica-Oblique", fontSize=9, spaceBefore=2,
                         spaceAfter=10)


def font(size):
    for name in ("DejaVuSans.ttf", "Arial.ttf", "Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def png(image: Image.Image) -> io.BytesIO:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    buffer.seek(0)
    return buffer


def fire_triangle() -> io.BytesIO:
    """Figure 1: a triangle with one colour per side (heat, oxygen, fuel) and a flame in the middle."""
    w, h = 900, 560
    im = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(im)
    top, left, right = (w // 2, 60), (190, 480), (w - 190, 480)
    d.polygon([top, left, right], fill=(255, 236, 214))
    d.line([left, top], fill=(200, 40, 20), width=14)    # heat
    d.line([top, right], fill=(40, 120, 200), width=14)  # oxygen
    d.line([right, left], fill=(120, 80, 40), width=14)  # fuel
    d.ellipse((w // 2 - 45, 290, w // 2 + 45, 400), fill=(240, 120, 30))
    d.ellipse((w // 2 - 25, 330, w // 2 + 25, 400), fill=(255, 200, 60))
    return png(im)


def extinguisher_chart() -> io.BytesIO:
    """Figure 2: bar chart of the fire classes each extinguisher type covers."""
    data = [("Water", 1), ("Foam", 2), ("CO2", 2), ("Dry powder", 4), ("Wet chemical", 2)]
    w, h = 900, 520
    im = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(im)
    small = font(24)
    base, left = 440, 120
    d.line([(left, 60), (left, base), (w - 40, base)], fill="black", width=3)
    for level in range(1, 5):
        y = base - level * 90
        d.line([(left - 8, y), (w - 40, y)], fill=(220, 220, 220), width=1)
        d.text((left - 40, y - 14), str(level), font=small, fill="black")
    bar = 110
    gap = (w - 40 - left - bar * len(data)) // (len(data) + 1)
    for i, (label, value) in enumerate(data):
        x = left + gap + i * (bar + gap)
        d.rectangle([x, base - value * 90, x + bar, base], fill=(30, 100, 170))
        tw = d.textlength(label, font=small)
        d.text((x + bar / 2 - tw / 2, base + 12), label, font=small, fill="black")
    return png(im)


def bullets(items, numbered=False):
    return ListFlowable(
        [ListItem(Paragraph(text, BODY), leftIndent=14) for text in items],
        bulletType="1" if numbered else "bullet",
        bulletFormat="%s." if numbered else None,
        start=None if numbered else "•",
        bulletFontName="Helvetica",
        bulletFontSize=10.5,
        leftIndent=14,
    )


def box(label, text):
    """A shaded paragraph, the way course material often prints a note."""
    style = ParagraphStyle("box", parent=BODY, backColor=colors.HexColor("#fff4ea"), borderPadding=6,
                           spaceBefore=8, spaceAfter=12, leftIndent=6, rightIndent=6)
    return Paragraph(f"<b>{label}:</b> {text}", style)


def data_table(rows, widths):
    body = [[Paragraph(c, CELL_HEAD) for c in rows[0]]] + [[Paragraph(c, CELL) for c in r] for r in rows[1:]]
    table = Table(body, colWidths=[w * mm for w in widths], repeatRows=1)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#999999")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9eef3")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return table


def page_decor(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.grey)
    canvas.drawString(20 * mm, A4[1] - 12 * mm, "Workplace Fire Safety | Module 1")
    canvas.drawRightString(A4[0] - 20 * mm, 12 * mm, f"Page {doc.page}")
    canvas.restoreState()


def story():
    s = [
        Paragraph("Workplace Fire Safety", TITLE),
        Paragraph("Module 1: Preventing and responding to fires", SUBTITLE),
        Paragraph("1 Introduction", H1),
        Paragraph(
            "Most workplace fires are preventable. This module explains <b>how a fire starts</b>, which "
            "<b>extinguisher</b> fits which fire, and what every employee does when the alarm sounds. "
            "It takes about 20 minutes and ends with a short set of learning checks.", BODY),
        Paragraph("1.1 Learning objectives", H2),
        Paragraph("After this module you can:", BODY),
        bullets([
            "Name the three elements a fire needs and explain how removing one stops the fire.",
            "Match the classes of fire to their typical fuels.",
            "Choose a suitable extinguisher and use it with the PASS technique.",
            "Follow the evacuation procedure of your building.",
        ]),
        Paragraph("2 How a fire starts", H1),
        Paragraph(
            "A fire needs <b>heat</b>, <b>fuel</b> and <b>oxygen</b> at the same time. Remove any one of the "
            "three and the fire goes out: water removes heat, a fire blanket removes oxygen, and closing a gas "
            "valve removes fuel.", BODY),
        PdfImage(fire_triangle(), width=110 * mm, height=110 * mm * 560 / 900),
        Paragraph("Figure 1: The fire triangle. The sides stand for heat (red), oxygen (blue) and fuel (brown).",
                  CAPTION),
        Paragraph("2.1 Classes of fire", H2),
        Paragraph(
            "Fires are grouped by the fuel that burns. The class tells you which extinguisher is safe to use.",
            BODY),
        data_table([
            ["Class", "Fuel", "Typical examples in an office or workshop"],
            ["A", "Solid materials", "Paper, cardboard, wood, textiles, plastics"],
            ["B", "Flammable liquids", "Solvents, paint thinners, fuel, cleaning alcohol"],
            ["C", "Flammable gases", "Propane cylinders, natural gas lines, aerosol cans"],
            ["D", "Combustible metals", "Magnesium and aluminium swarf from machining"],
            ["F", "Cooking oils and fats", "Deep fryers and pans in staff kitchens"],
            ["Electrical", "Live equipment (not a class of its own)", "Overheated chargers, extension leads, "
             "server racks, distribution boards"],
            ["Mixed", "More than one fuel", "Waste bins with paper and solvent rags, storage rooms"],
        ], [24, 52, 94]),
        box("Note", "Never use water on a fire that involves live electrical equipment or cooking oil. "
                    "Switch off the power first if it is safe to do so."),
        Paragraph("3 Responding to a fire", H1),
        Paragraph("When you discover a fire, act in this order:", BODY),
        bullets([
            "Raise the alarm by pressing the nearest manual call point.",
            "Call the emergency services and give the address of the building.",
            "Decide whether the fire is small enough to fight safely.",
            "If it is not, close the doors behind you and leave the building.",
        ], numbered=True),
        Paragraph("3.1 Using an extinguisher", H2),
        Paragraph(
            "Use an extinguisher only on a small fire and only if your escape route stays behind you. "
            "Remember the <b>PASS</b> technique: <b>Pull</b> the pin, <b>Aim</b> at the base of the fire, "
            "<b>Squeeze</b> the handle and <b>Sweep</b> from side to side.", BODY),
        PdfImage(extinguisher_chart(), width=110 * mm, height=110 * mm * 520 / 900),
        Paragraph("Figure 2: Number of fire classes each extinguisher type is suitable for.", CAPTION),
        data_table([
            ["Extinguisher", "Label colour", "Use on", "Do not use on"],
            ["Water", "Red", "Class A", "Electrical, B, F"],
            ["Foam", "Cream", "Classes A and B", "Electrical, F"],
            ["CO2", "Black", "Class B, electrical", "Class F, enclosed rooms"],
            ["Dry powder", "Blue", "Classes A, B, C, electrical", "Indoors where possible"],
            ["Wet chemical", "Yellow", "Classes A and F", "Electrical, B"],
        ], [38, 30, 52, 50]),
        box("Remember", "Your safety comes first. Only fight a fire if you have been trained and the fire is "
                        "no bigger than a waste bin."),
        Paragraph("4 Evacuation", H1),
        Paragraph(
            "Every building has an evacuation plan with <b>escape routes</b> and an <b>assembly point</b>. "
            "Read the plan near the entrance of your floor.", BODY),
        bullets([
            "Leave by the nearest safe exit and do not use the lifts.",
            "Help visitors who do not know the building.",
            "Report to the fire warden at the assembly point.",
            "Do not go back into the building until the fire service says it is safe.",
        ]),
    ]
    return s


def main() -> None:
    doc = SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=20 * mm,
                            bottomMargin=20 * mm, title="Workplace Fire Safety, Module 1",
                            author="Sample generator", invariant=1)
    doc.build(story(), onFirstPage=page_decor, onLaterPages=page_decor)
    print("wrote", OUT.name)


if __name__ == "__main__":
    main()
