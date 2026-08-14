from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


def valid_png(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 32:
        return False
    with path.open("rb") as stream:
        return stream.read(8) == b"\x89PNG\r\n\x1a\n"


def zotero_rect_to_mupdf(rect: list[float], page_height: float) -> tuple[float, float, float, float]:
    """Convert Zotero's bottom-left PDF coordinates to MuPDF's top-left coordinates."""
    x0, y0, x1, y1 = (float(value) for value in rect)
    return x0, page_height - y1, x1, page_height - y0


def crop_image_annotation(
    pdf_path: Path,
    position: str | dict[str, Any],
    output_path: Path,
    scale: float = 4.0,
) -> Path:
    if valid_png(output_path):
        return output_path

    try:
        import pymupdf as fitz
    except ImportError:
        import fitz  # type: ignore[no-redef]

    parsed = json.loads(position) if isinstance(position, str) else position
    page_index = int(parsed["pageIndex"])
    rects = parsed.get("rects") or []
    if not rects:
        raise ValueError("image annotation has no rectangles")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.tmp.png")
    with fitz.open(pdf_path) as document:
        page = document[page_index]
        page_height = float(page.cropbox.height)
        converted = [zotero_rect_to_mupdf(rect, page_height) for rect in rects]
        clip = fitz.Rect(
            min(rect[0] for rect in converted),
            min(rect[1] for rect in converted),
            max(rect[2] for rect in converted),
            max(rect[3] for rect in converted),
        )
        if clip.is_empty or clip.is_infinite:
            raise ValueError("image annotation rectangle is invalid")
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=True)
        pixmap.save(temporary)

    # PyMuPDF sometimes rounds the outside edge up. Trim to Zotero's floor-sized crop.
    expected_width = max(1, math.floor(clip.width * scale))
    expected_height = max(1, math.floor(clip.height * scale))
    if pixmap.width != expected_width or pixmap.height != expected_height:
        try:
            from PIL import Image

            with Image.open(temporary) as image:
                image.crop((0, 0, expected_width, expected_height)).save(temporary)
        except ImportError:
            pass
    temporary.replace(output_path)
    return output_path

