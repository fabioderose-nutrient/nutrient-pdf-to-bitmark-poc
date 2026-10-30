"""Document structure extraction with the Nutrient Python SDK.

One pass over the PDF gives:
  - the layout elements (headings with level, paragraphs, list items, tables
    with cells, pictures) in reading order with bounding boxes at 200 dpi
  - one PNG per page in the same pixel space (used for figure crops and as
    a fallback renderer)

Everything downstream (bitmark writer, provenance, quiz grounding) works on
the plain Python structure returned by `extract()`.
"""

import json
import os
import re
import shutil
from pathlib import Path

import nutrient_sdk as ns

DPI = 200
_licensed = False


def register_license():
    """Register NUTRIENT_LICENSE_KEY once. Without it the SDK runs in evaluation mode."""
    global _licensed
    if _licensed:
        return
    key = os.environ.get("NUTRIENT_LICENSE_KEY", "").strip()
    if key:
        ns.License.register_key(key)
    _licensed = True


_NUL_HEX = re.compile(r"\x00([0-9a-fA-F]{2})")


def _clean_native_json(text):
    """The native writer leaves a NUL byte + 2 hex digits for some chars."""
    text = _NUL_HEX.sub(lambda m: chr(int(m.group(1), 16)), text)
    return text.replace("\x00", "")


def _layout_threshold():
    try:
        return float(os.environ.get("LAYOUT_CONFIDENCE", "0.2"))
    except ValueError:
        return 0.2


def extract(pdf_path, work_dir):
    """Run layout analysis and page rendering. Returns a dict:

    {
      "pages": [{"page": 1, "width": px, "height": px, "image": "pages/p1.png"}],
      "elements": [ {type, role, headingLevel, text, words, bounds, pageNumber,
                     readingOrder, cells, confidence, ...} ... ]
    }
    """
    register_license()
    work_dir = Path(work_dir)
    pages_dir = work_dir / "pages"
    if pages_dir.exists():
        shutil.rmtree(pages_dir)
    pages_dir.mkdir(parents=True)

    doc = ns.Document.open(str(pdf_path))
    try:
        settings = doc.settings
        ces = settings.content_extraction_settings
        ces.enable_image_extraction = True
        ces.enable_table_extraction = True
        settings.inference_layout_settings.confidence_threshold = _layout_threshold()
        # Note: do not enable VisionFeatures.TABLE here. Combined with
        # enable_image_extraction it suppresses every "picture" element
        # (verified with SDK 1.0.12); the generic layout model already
        # returns tables with cells.

        vision = ns.Vision.set(doc)
        raw = vision.extract_content()
        layout = json.loads(_clean_native_json(raw))

        # Page renders in the same 200 dpi pixel space as the element bounds.
        img = settings.image_settings
        img.export_format = ns.ImageExportFormat.PNG
        img.rasterization_dpi = DPI
        doc.export_as_image(str(pages_dir / "page.png"))
        page_count = doc.page_count
    finally:
        doc.close()

    # export_as_image writes page.png for a single page and page-<n>.png
    # (or page-<n>.png variants) for several: map whatever it produced.
    rendered = {}
    produced = sorted(pages_dir.glob("*.png"))
    if page_count == 1 and produced:
        rendered[1] = f"pages/{produced[0].name}"
    else:
        for p in produced:
            m = re.search(r"(\d+)\.png$", p.name)
            if m:
                rendered[int(m.group(1))] = f"pages/{p.name}"
        if rendered and min(rendered) == 0:  # zero-based naming
            rendered = {n + 1: v for n, v in rendered.items()}

    pages = []
    for meta in layout.get("metadata", []):
        n = int(meta.get("pageNumber", 0))
        pages.append({
            "page": n,
            "width": meta.get("width"),
            "height": meta.get("height"),
            "image": rendered.get(n),
        })
    pages.sort(key=lambda p: p["page"])

    elements = layout.get("elements", [])
    elements.sort(key=lambda e: (e.get("pageNumber", 0), e.get("readingOrder", 0)))
    return {"pages": pages, "elements": elements}


def element_box(element):
    """Tight bounding box from the words when available (the element bounds
    of multi-line paragraphs are sometimes too wide), else the element bounds."""
    words = element.get("words") or []
    if words:
        xs = [w["bounds"]["x"] for w in words]
        ys = [w["bounds"]["y"] for w in words]
        x2 = [w["bounds"]["x"] + w["bounds"]["width"] for w in words]
        y2 = [w["bounds"]["y"] + w["bounds"]["height"] for w in words]
        return {"x": min(xs), "y": min(ys), "width": max(x2) - min(xs), "height": max(y2) - min(ys)}
    b = element.get("bounds") or {}
    return {"x": b.get("x", 0), "y": b.get("y", 0), "width": b.get("width", 0), "height": b.get("height", 0)}


def crop_picture(page_image_path, bounds, out_path, page_size=None, pad=4):
    """Crop a figure out of the page render. Pillow only touches PNG bytes."""
    from PIL import Image

    with Image.open(page_image_path) as im:
        w, h = im.size
        x1 = max(0, int(bounds["x"]) - pad)
        y1 = max(0, int(bounds["y"]) - pad)
        x2 = min(w, int(bounds["x"] + bounds["width"]) + pad)
        y2 = min(h, int(bounds["y"] + bounds["height"]) + pad)
        if x2 - x1 < 8 or y2 - y1 < 8:
            return None
        crop = im.crop((x1, y1, x2, y2))
        crop.save(out_path)
        return {"width": x2 - x1, "height": y2 - y1}
