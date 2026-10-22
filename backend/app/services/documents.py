"""Safe local storage for uploaded files. Originals are immutable, hashed and kept outside any served path."""
import hashlib
import re
import uuid
from pathlib import Path

import pymupdf

ALLOWED = {".pdf": b"%PDF", ".png": b"\x89PNG", ".jpg": b"\xff\xd8", ".jpeg": b"\xff\xd8"}


class UploadError(ValueError):
    pass


def save_upload(root: str, filename: str, data: bytes, max_mb: int = 15, max_pages: int = 40) -> dict:
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED:
        raise UploadError("Unsupported file type. Upload a PDF, PNG or JPG.")
    if len(data) > max_mb * 1024 * 1024:
        raise UploadError(f"File is larger than {max_mb} MB.")
    if not data.startswith(ALLOWED[ext]):
        raise UploadError("File content does not match its extension.")
    doc_id = uuid.uuid4().hex
    folder = Path(root).resolve() / doc_id  # server-generated path; user filename is never used on disk
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"original{ext}"
    path.write_bytes(data)
    pages = 1
    if ext == ".pdf":
        try:
            pages = len(pymupdf.open(path))
        except Exception as e:
            raise UploadError("The PDF could not be opened.") from e
        if pages > max_pages:
            raise UploadError(f"PDF has more than {max_pages} pages.")
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename).name)[:120]
    return {"id": doc_id, "path": str(path), "ext": ext, "sha256": hashlib.sha256(data).hexdigest(),
            "filename": safe_name, "size": len(data), "page_count": pages}


def render_page_png(path: str, page: int) -> bytes:
    p = Path(path)
    if p.suffix.lower() == ".pdf":
        doc = pymupdf.open(p)
        if not 1 <= page <= len(doc):
            raise UploadError("No such page.")
        return doc[page - 1].get_pixmap(dpi=110).tobytes("png")
    if page != 1:
        raise UploadError("No such page.")
    import io

    from PIL import Image
    buf = io.BytesIO()
    Image.open(p).convert("RGB").save(buf, "PNG")
    return buf.getvalue()
