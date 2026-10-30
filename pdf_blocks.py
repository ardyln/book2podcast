"""Run by book2podcast inside the Docling venv: PDF -> JSON text blocks.

  pdf_blocks.py book.pdf out.json

Docling's layout model separates headings, body text and page furniture
(running headers, footers, page numbers); bookmarks come from the PDF outline.
Text the PDF already carries is kept (a scanner's commercial OCR layer beats
RapidOCR on old type); RapidOCR only reads regions that have no text, which
for an image-only scan is every page.
Output: {"meta": {title, author}, "pages": n, "scanned": bool,
         "blocks": [{text, level, page, top, bottom}], "toc": [{title, page, level}]}
top/bottom are the block's edges as a fraction of the page height from the top.
"""

import gc
import json
import logging
import sys
import time

import pypdfium2
import pypdfium2.raw as pdfium_c
import torch
from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import HeadingHierarchyOptions, OcrMode, PdfPipelineOptions, RapidOcrOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import DocItemLabel

BODY = {DocItemLabel.TEXT, DocItemLabel.PARAGRAPH, DocItemLabel.LIST_ITEM, DocItemLabel.CODE}

logging.basicConfig(level=logging.ERROR)
src, out = sys.argv[1], sys.argv[2]


def is_scanned(pdf):
    """True when sampled pages are each covered by a single large image."""
    sample = range(min(len(pdf), 10), min(len(pdf), 30))
    hits = 0
    for i in sample:
        page = pdf[i]
        w, h = page.get_size()
        for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE]):
            l, b, r, t = obj.get_bounds()
            if (r - l) * (t - b) > 0.6 * w * h:
                hits += 1
                break
    return bool(sample) and hits >= 0.8 * len(sample)


pdf = pypdfium2.PdfDocument(src)
md = pdf.get_metadata_dict()
toc = []
for bm in pdf.get_toc():
    dest, title = bm.get_dest(), " ".join((bm.get_title() or "").split())
    if dest is not None and dest.get_index() is not None and title:
        toc.append({"title": title, "page": dest.get_index() + 1, "level": bm.level})
pages = len(pdf)
scanned = is_scanned(pdf)
pdf.close()

device = AcceleratorDevice.CUDA if torch.cuda.is_available() else AcceleratorDevice.CPU
print(f"docling: {pages} pages on {device.value}{' (scanned book)' if scanned else ''}",
      file=sys.stderr, flush=True)
# Heading hierarchy: Docling infers real heading levels from bookmarks, numbering
# (Part > Chapter > 1.1) and font size/weight, instead of every heading at level 1.
opts = PdfPipelineOptions(
    do_table_structure=False, do_ocr=True,
    ocr_options=RapidOcrOptions(backend="torch", lang=["en"], mode=OcrMode.DEFAULT),
    heading_hierarchy_options=HeadingHierarchyOptions(enabled=True),
    # font-size levels need the parsed pages, which stay in memory (GBs on an
    # 800-page scan); book2podcast ignores levels on scans anyway
    generate_parsed_pages=not scanned,
    accelerator_options=AcceleratorOptions(device=device))
converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})

# Convert in chunks: a whole-book conversion keeps every page's cells and layout
# in memory until the end (GBs for a 600-800 page scan). Chunks keep memory flat;
# the loaded models are reused, only the per-page data is dropped between chunks.
CHUNK = 40
blocks = []
started = time.time()
for first in range(1, pages + 1, CHUNK):
    last = min(first + CHUNK - 1, pages)
    done = first - 1
    left = ""
    if done:
        secs = int((time.time() - started) / done * (pages - done))
        left = f" · ~{secs // 60} min {secs % 60:02d} s left"
    print(f"  docling: pages {first}-{last} of {pages} ({100 * done // pages}%){left}", file=sys.stderr, flush=True)
    doc = converter.convert(src, page_range=(first, last)).document
    for item, _ in doc.iterate_items():
        text = " ".join((getattr(item, "text", "") or "").split())
        if not text or not item.prov:
            continue
        if item.label == DocItemLabel.TITLE:
            level = 1
        elif item.label == DocItemLabel.SECTION_HEADER:
            level = min(getattr(item, "level", 1) or 1, 6)
        elif item.label in BODY:
            level = 0
        else:   # pictures, tables, captions, footnotes, page headers/footers
            continue
        prov = item.prov[0]
        height = doc.pages[prov.page_no].size.height
        box = prov.bbox.to_top_left_origin(height)
        blocks.append({"text": text, "level": level, "page": prov.page_no,
                       "top": round(box.t / height, 3), "bottom": round(box.b / height, 3)})
    del doc
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

with open(out, "w") as f:
    json.dump({"meta": {"title": (md.get("Title") or "").strip(), "author": (md.get("Author") or "").strip()},
               "pages": pages, "scanned": scanned, "blocks": blocks, "toc": toc}, f, ensure_ascii=False)
