"""Deterministic mapping from Nutrient layout elements to bitmark markup.

Rules (bitmark 3, validated with the bitmark Association's official parser):

  SectionHeader                -> [.chapter] with [@level:n]
  first SectionHeader          -> [.book] title (+ subtitle when the next
                                  paragraph is a short one-liner)
  Text / ListItem runs         -> [.article] body (bitmark++ text: bold runs,
                                  bullet lists, numbered lists)
  Text starting with "Note:"   -> [.remark] (also Remember, Tip, Warning ...,
                                  and the German Merke, Hinweis, Achtung ...)
  table (cells)                -> [.table] with header row as columns
  picture                      -> [.image] cropped from the page render,
                                  with the following caption when present

Every bit carries [@id:...] = hash of its normalised content, so a bit that
does not change keeps its id when the PDF is converted again (annotations and
comments attached to the bit survive a content refresh). A provenance map
(bit id -> page + bounding box) is produced alongside the markup.
"""

import hashlib
import re
import unicodedata
from pathlib import Path

from structure import crop_picture, element_box

SKIP_ROLES = {"PageHeader", "PageFooter", "Header", "Footer", "PageNumber", "Footnote"}
# Labels that open a remark box or a caption. English first, then German and French equivalents,
# so course material in those languages converts the same way.
REMARK_PREFIXES = ("note", "remember", "tip", "warning", "caution", "important", "example", "attention",
                   "merke", "hinweis", "achtung", "wichtig", "tipp", "beispiel", "remarque")
CAPTION_PREFIXES = ("fig", "figure", "table", "source",
                    "abb", "abbildung", "bild", "grafik", "tabelle", "quelle")
NUMBERING = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+")
ORDERED_ITEM = re.compile(r"^(\d+[\.\)]|[a-zA-Z][\.\)])\s+")
# bullets: typographic bullets, dashes, asterisks and the private-use glyphs
# that Word's Symbol/Wingdings bullets become in the text layer, plus the DEL
# character (\x7f) that some PDF generators use for the bullet glyph
BULLET_ITEM = re.compile(r"^[•●◦▪■–\-\*·--\x7f]+\s*")


# ----------------------------------------------------------------- helpers

# A hyphen before a conjunction is a suspended hyphen and stays ("pre- and post-"). The list
# covers English, German (und, oder, bzw, sowie) and French (et, ou).
HYPHEN_SYLLABLE = re.compile(r"(\w)-\n(?!(?:und|oder|bzw|sowie|et|ou|and|or)\b)(?=[a-zäöüß])")
HYPHEN_COMPOUND = re.compile(r"(\w)-\n(?=[A-ZÄÖÜ])")


def normalise(text):
    """Collapse whitespace, resolve ligatures (ﬁ, ﬂ ...) and re-join words
    broken at a line break: a syllable break drops the hyphen, a hyphen
    followed by a capital letter (a compound such as "Fire-\\nSafety") keeps
    it; suspended hyphens like "pre- and" are left alone."""
    text = unicodedata.normalize("NFKC", text or "")
    text = HYPHEN_SYLLABLE.sub(r"\1", text)
    text = HYPHEN_COMPOUND.sub(r"\1-", text)
    return re.sub(r"\s+", " ", text).strip()


def stable_id(prefix, *parts):
    digest = hashlib.sha1("\x1f".join(normalise(p) for p in parts).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:10]}"


def breakscape(text):
    """Minimal breakscaping for bitmark++ body text: neutralise the inline
    marks and the line-start list characters that occur in normal prose."""
    text = text.replace("**", "*^*").replace("__", "_^_").replace("==", "=^=").replace("!!", "!^!")
    out = []
    for line in text.split("\n"):
        if line[:1] in ("•", "|", "#", "+", "-") or line.startswith("•"):
            line = "^" + line
        out.append(line)
    return "\n".join(out)


def property_value(text):
    """Property values live inside [@x:...]: brackets must not close early."""
    return normalise(text).replace("]", "]​")


def words_to_text(element):
    """Rebuild the paragraph text from the words, wrapping bold runs with **.
    The result is already breakscaped (escaping happens per chunk so the
    bold marks themselves survive). Falls back to the plain element text."""
    words = element.get("words") or []
    if not words or not any(w.get("bold") for w in words) or all(w.get("bold") for w in words):
        return breakscape(normalise(element.get("text", "")))
    parts, run, run_bold = [], [], None
    for w in words:
        bold = bool(w.get("bold"))
        if run and bold != run_bold:
            parts.append((run_bold, " ".join(run)))
            run = []
        run.append(w.get("text", ""))
        run_bold = bold
    if run:
        parts.append((run_bold, " ".join(run)))
    text = ""
    for bold, chunk in parts:
        chunk = breakscape(normalise(chunk))
        if not chunk:
            continue
        text += (" " if text else "") + (f"**{chunk}**" if bold else chunk)
    return text


def merge_list_continuations(elements):
    """The layout model sometimes splits a wrapped list item into two
    ListItems (or a ListItem + a Text line). Glue a fragment back to the
    previous item when the previous item has no sentence end and the
    fragment carries no list marker."""
    out = []
    for el in elements:
        prev = out[-1] if out else None
        role = el.get("role")
        raw = normalise(el.get("text", ""))
        if prev and prev.get("role") == "ListItem" and role in ("ListItem", "Text") and raw:
            prev_text = normalise(prev.get("text", ""))
            has_marker = bool(ORDERED_ITEM.match(raw) or BULLET_ITEM.match(raw)) and raw[:1] not in "-–"
            if not has_marker and prev_text and prev_text[-1] not in ".!?:;" and (role == "ListItem" or raw[:1].islower()):
                merged = dict(prev)
                merged["text"] = prev_text + " " + raw
                merged["words"] = (prev.get("words") or []) + (el.get("words") or [])
                out[-1] = merged
                continue
        out.append(el)
    return out


def order_list_run(items):
    """Numbered items come back in the SDK reading order, which is not
    always the visual order: sort by the number they carry, else by y."""
    numbers = []
    for el in items:
        m = re.match(r"^(\d+)[\.\)]\s", normalise(el.get("text", "")))
        numbers.append(int(m.group(1)) if m else None)
    if all(n is not None for n in numbers):
        return [el for _, el in sorted(zip(numbers, items), key=lambda p: p[0])]
    return sorted(items, key=lambda e: (e.get("pageNumber", 0), element_box(e)["y"]))


def heading_level(text, fallback):
    """Prefer explicit numbering ("2.1 Title" -> level 2), else the SDK level."""
    m = NUMBERING.match(text or "")
    if m:
        return min(7, m.group(1).count(".") + 1)
    return fallback


# ----------------------------------------------------------------- blocks

class Bit:
    def __init__(self, kind, bit_id, markup, page=None, box=None, meta=None):
        self.kind = kind
        self.id = bit_id
        self.markup = markup
        self.page = page
        self.box = box
        self.meta = meta or {}

    def provenance(self):
        return {"id": self.id, "type": self.kind, "page": self.page, "box": self.box, **self.meta}


def _article(elements):
    """One article bit from a run of Text / ListItem elements."""
    elements = merge_list_continuations(elements)
    # reorder each run of consecutive list items
    ordered = []
    run_items = []
    for el in elements + [None]:
        if el is not None and el.get("role") == "ListItem":
            run_items.append(el)
            continue
        if run_items:
            ordered.extend(order_list_run(run_items))
            run_items = []
        if el is not None:
            ordered.append(el)
    elements = ordered

    lines = []
    plain = []
    in_list = None
    for el in elements:
        text = words_to_text(el)
        if not text:
            continue
        plain.append(normalise(el.get("text", "")))
        role = el.get("role")
        if role == "ListItem":
            raw = normalise(el.get("text", ""))
            if ORDERED_ITEM.match(raw):
                text = ORDERED_ITEM.sub("", text, count=1)
                prefix = "•1 "
            else:
                text = BULLET_ITEM.sub("", text.lstrip("^"), count=1)
                prefix = "• "
            if in_list is None and lines:
                lines.append("")
            lines.append(prefix + text)
            in_list = True
        else:
            if lines:
                lines.append("")
            lines.append(text)
            in_list = None
    if not lines:
        return None
    body = "\n".join(lines)
    bit_id = stable_id("a", *plain)
    first = elements[0]
    box = _union_box(elements)
    markup = f"[.article][@id:{bit_id}]\n{body}"
    return Bit("article", bit_id, markup, first.get("pageNumber"), box,
               {"elements": len(elements)})


def _union_box(elements):
    boxes = [element_box(e) for e in elements]
    x1 = min(b["x"] for b in boxes)
    y1 = min(b["y"] for b in boxes)
    x2 = max(b["x"] + b["width"] for b in boxes)
    y2 = max(b["y"] + b["height"] for b in boxes)
    return {"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1}


def _remark(el):
    text = normalise(el.get("text", ""))
    m = re.match(r"^([A-Za-zÄÖÜäöü]+)\s*[:\-–]\s*(.+)$", text)
    title, body = (m.group(1), m.group(2)) if m else ("Note", text)
    bit_id = stable_id("r", text)
    markup = f"[.remark][@id:{bit_id}][#{property_value(title)}]\n{breakscape(body)}"
    return Bit("remark", bit_id, markup, el.get("pageNumber"), element_box(el))


def _split_table(el):
    """The table detector likes to absorb the boxed paragraph that follows a
    table ("Note: ..."), as a row with one filled cell. Return the real
    rows plus the stray paragraphs as separate elements."""
    rows = {}
    for cell in el.get("cells", []):
        rows.setdefault(cell.get("row", 0), {})[cell.get("column", 0)] = cell
    ordered = [rows[r] for r in sorted(rows)]
    strays = []
    while ordered:
        filled = [c for c in ordered[-1].values() if normalise(c.get("text", ""))]
        if len(filled) == 1 and len(ordered[-1]) > 1 or (len(filled) == 1 and len(ordered) == 1):
            cell = filled[0]
            strays.insert(0, {"type": "paragraph", "role": "Text", "text": cell.get("text", ""),
                              "words": cell.get("words", []), "bounds": cell.get("bounds"),
                              "pageNumber": el.get("pageNumber"), "readingOrder": el.get("readingOrder")})
            ordered.pop()
        else:
            break
    return ordered, strays


def _table(el, ordered=None):
    if ordered is None:
        ordered, _ = _split_table(el)
    if not ordered:
        return None
    n_cols = max((c for r in ordered for c in r), default=0) + 1
    header = ordered[0] if all(c.get("isHeader") for c in ordered[0].values()) else None
    body_rows = ordered[1:] if header else ordered
    texts = []
    lines = []
    if header:
        lines.append("===")
        cells = []
        for c in range(n_cols):
            t = normalise(header.get(c, {}).get("text", ""))
            texts.append(t)
            cells.append(f"[#{property_value(t)}]" if t else "")
        lines.append("\n==\n".join(cells))
    for row in body_rows:
        lines.append("===")
        cells = []
        for c in range(n_cols):
            t = normalise(row.get(c, {}).get("text", ""))
            texts.append(t)
            cells.append(breakscape(t))
        lines.append("\n==\n".join(cells))
    lines.append("===")
    bit_id = stable_id("t", *texts)
    markup = f"[.table][@id:{bit_id}]\n" + "\n".join(lines)
    return Bit("table", bit_id, markup, el.get("pageNumber"), el.get("bounds"),
               {"rows": len(ordered), "columns": n_cols})


def _image(el, caption, pages, work_dir, in_figure=None):
    page = next((p for p in pages if p["page"] == el.get("pageNumber")), None)
    if not page or not page.get("image"):
        return None
    images_dir = Path(work_dir) / "images"
    images_dir.mkdir(exist_ok=True)
    caption_text = normalise(caption.get("text", "")) if caption else ""
    bounds = el.get("bounds") or {}
    if in_figure:   # labels drawn in the figure: crop them with it
        bounds = _union_box([el] + in_figure)
    bit_id = stable_id("i", caption_text, str(round(bounds.get("width", 0))), str(round(bounds.get("height", 0))), str(el.get("pageNumber")))
    out = images_dir / f"{bit_id}.png"
    size = crop_picture(Path(work_dir) / page["image"], bounds, out)
    if not size:
        return None
    alt = caption_text or el.get("altDescription") or "Figure"
    markup = (f"[.image][@id:{bit_id}][&image:images/{bit_id}.png]"
              f"[@width:{size['width']}][@height:{size['height']}][@alt:{property_value(alt)}]")
    if caption_text:
        markup += f"[@caption:{property_value(caption_text)}]"
    box = dict(bounds)
    if caption and caption.get("pageNumber") == el.get("pageNumber"):
        box = _union_box([el, caption])
    return Bit("image", bit_id, markup, el.get("pageNumber"), box, {"src": f"images/{bit_id}.png"})


def _inside(inner, outer, min_overlap=0.5):
    """True when at least half of `inner` lies within `outer` (text drawn in
    a figure often sits on its bottom edge, so a strict test misses it)."""
    if not inner or not outer or inner["width"] <= 0 or inner["height"] <= 0:
        return False
    ox = max(0, min(inner["x"] + inner["width"], outer["x"] + outer["width"]) - max(inner["x"], outer["x"]))
    oy = max(0, min(inner["y"] + inner["height"], outer["y"] + outer["height"]) - max(inner["y"], outer["y"]))
    return (ox * oy) / (inner["width"] * inner["height"]) >= min_overlap


def _is_caption(text_el, picture_el):
    """A Caption-role element, or a short Text line right under the figure."""
    if not text_el or text_el.get("role") not in ("Text", "Caption"):
        return False
    text = normalise(text_el.get("text", ""))
    if not text or len(text) > 160:
        return False
    if text_el.get("role") == "Caption":
        return True
    tb = element_box(text_el)
    pb = picture_el.get("bounds") or {}
    gap = tb["y"] - (pb.get("y", 0) + pb.get("height", 0))
    starts_like_caption = text.lower().startswith(CAPTION_PREFIXES)
    return (0 <= gap <= 120 and starts_like_caption) or (0 <= gap <= 40 and len(text) < 90)


TINY_TEXT_PX = 18   # at 200 dpi: below ~6.5 pt, i.e. labels drawn inside a figure


def _figure_tail(elements, i):
    """After picture i: separate the lines the layout model read inside or
    right under the figure (labels, axis text: tiny or overlapping) from the
    caption. Returns (caption element or None, in-figure elements, index of
    the first element after the figure)."""
    pic = elements[i]
    pb = pic.get("bounds") or {}
    bottom = pb.get("y", 0) + pb.get("height", 0)
    j = i + 1
    candidates = []
    in_figure = []
    while j < len(elements):
        el = elements[j]
        if el.get("type") != "paragraph" or el.get("role") not in ("Text", "Caption"):
            break
        same_page = el.get("pageNumber") == pic.get("pageNumber")
        box = element_box(el)
        text = normalise(el.get("text", ""))
        if same_page and (_inside(box, pb) or (box["height"] < TINY_TEXT_PX and 0 <= box["y"] - bottom <= 60)):
            in_figure.append(el)
            bottom = max(bottom, box["y"] + box["height"])
            j += 1
            continue
        if _is_caption(el, pic) and (same_page or el.get("role") == "Caption") and len(candidates) < 2 and text:
            candidates.append(el)
            j += 1
            continue
        break
    caption = None
    if candidates:
        ranked = sorted(candidates, key=lambda e: (e.get("role") != "Caption",
                                                   not normalise(e.get("text", "")).lower().startswith(CAPTION_PREFIXES)))
        caption = ranked[0]
    # candidates that lost are ordinary text again: hand them back
    leftovers = [c for c in candidates if c is not caption]
    return caption, in_figure, leftovers, j


def merge_split_tables(elements):
    """A table that continues on the next page comes back as a second table
    element with the same column count: append its rows to the first one
    (dropping a repeated header row)."""
    out = []
    for el in elements:
        prev = out[-1] if out else None
        if (prev and prev.get("type") == "table" and el.get("type") == "table"
                and prev.get("columnCount") == el.get("columnCount")
                and el.get("pageNumber", 0) == prev.get("pageNumber", 0) + 1):
            merged = dict(prev)
            offset = prev.get("rowCount", 0)
            cells = list(prev.get("cells", []))
            first_row = [c for c in el.get("cells", []) if c.get("row") == 0]
            skip_header = bool(first_row) and all(c.get("isHeader") for c in first_row)
            for c in el.get("cells", []):
                if skip_header and c.get("row") == 0:
                    continue
                c2 = dict(c)
                c2["row"] = c.get("row", 0) + offset - (1 if skip_header else 0)
                cells.append(c2)
            merged["cells"] = cells
            merged["rowCount"] = offset + el.get("rowCount", 0) - (1 if skip_header else 0)
            merged["continued_on"] = el.get("pageNumber")
            out[-1] = merged
            continue
        out.append(el)
    return out


def _is_noise(el):
    """Tiny or low-confidence pictures (a ruling or a shaded cell read as a figure). They are
    dropped before tables are merged, so they do not split a table that continues on the next page."""
    if el.get("type") != "picture":
        return False
    b = el.get("bounds") or {}
    return b.get("width", 0) < 40 or b.get("height", 0) < 40 or (el.get("confidence") or 1) < 0.4


def _is_remark(el):
    text = normalise(el.get("text", "")).lower()
    return el.get("role") == "Text" and text.startswith(REMARK_PREFIXES) and ":" in text[:16]


# ----------------------------------------------------------------- main

def build(structure, work_dir, title_hint=None, language="en"):
    """Return (bits, book_info). `bits` is the ordered list of Bit objects
    (book header first). Chapters carry meta["level"]."""
    elements = [e for e in structure["elements"] if e.get("role") not in SKIP_ROLES and not _is_noise(e)]
    elements = merge_split_tables(elements)
    pages = structure["pages"]

    # Book title: first heading of the document (or the file name).
    title = title_hint or "Document"
    subtitle = None
    start = 0
    headers = [i for i, e in enumerate(elements) if e.get("role") == "SectionHeader"]
    if headers and headers[0] <= 2:
        title = normalise(elements[headers[0]].get("text", ""))
        start = headers[0] + 1
        # subtitle: a short un-numbered line right under the title (the
        # layout model labels an italic subtitle Text, Title or SectionHeader)
        if start < len(elements) and elements[start].get("role") in ("Text", "Title", "SectionHeader"):
            cand = normalise(elements[start].get("text", ""))
            if len(cand) <= 90 and not cand.endswith(".") and not NUMBERING.match(cand):
                subtitle = cand
                start += 1

    bits = []
    book_id = stable_id("book", title, subtitle or "")
    head = f"[.book:book][@id:{book_id}][#{property_value(title)}]"
    if subtitle:
        head += f"[##{property_value(subtitle)}]"
    head += f"[@language:{language}]"
    bits.append(Bit("book", book_id, head, 1, element_box(elements[headers[0]]) if headers else None))
    bits.append(Bit("toc", "toc", "[.toc]"))

    # Normalise heading levels so the smallest level becomes 1.
    levels = []
    for e in elements[start:]:
        if e.get("role") == "SectionHeader":
            levels.append(heading_level(normalise(e.get("text", "")), e.get("headingLevel") or 2))
    base = min(levels) if levels else 1

    run = []
    i = start
    while i < len(elements):
        el = elements[i]
        role, kind = el.get("role"), el.get("type")

        def flush():
            nonlocal run
            if run:
                bit = _article(run)
                if bit:
                    bits.append(bit)
                run = []

        if role == "SectionHeader":
            flush()
            text = normalise(el.get("text", ""))
            level = heading_level(text, el.get("headingLevel") or 2) - base + 1
            cid = stable_id("ch", text)
            bits.append(Bit("chapter", cid, f"[.chapter][@id:{cid}][@level:{level}][#{property_value(text)}]",
                            el.get("pageNumber"), element_box(el), {"level": level, "title": text}))
        elif kind == "table":
            flush()
            rows, strays = _split_table(el)
            bit = _table(el, rows)
            if bit:
                bits.append(bit)
            for stray in strays:
                if _is_remark(stray):
                    bits.append(_remark(stray))
                else:
                    run.append(stray)
        elif kind == "picture":
            flush()
            b = el.get("bounds") or {}
            if b.get("width", 0) < 40 or b.get("height", 0) < 40 or (el.get("confidence") or 1) < 0.4:
                i += 1
                continue
            caption, in_figure, leftovers, next_i = _figure_tail(elements, i)
            bit = _image(el, caption, pages, work_dir, in_figure)
            if bit:
                bits.append(bit)
            run.extend(leftovers)
            i = next_i
            continue
        elif _is_remark(el):
            flush()
            bits.append(_remark(el))
        elif role in ("Text", "ListItem", "Title", "Caption"):
            if run and run[-1].get("pageNumber") != el.get("pageNumber") and role != "ListItem":
                flush()
            run.append(el)
        i += 1
    flush()

    return bits, {"title": title, "subtitle": subtitle, "id": book_id}


def render(bits):
    return "\n\n".join(b.markup for b in bits) + "\n"
