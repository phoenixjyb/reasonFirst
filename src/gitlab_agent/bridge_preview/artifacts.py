from __future__ import annotations

import base64
from io import BytesIO
import json
import mimetypes
import os
from pathlib import Path
import stat
import xml.etree.ElementTree as ET
import zipfile
from typing import Any

TEXT_EXTENSIONS = {
    ".md", ".txt", ".json", ".jsonl", ".csv", ".tsv", ".yaml", ".yml",
    ".xml", ".html", ".htm", ".log", ".svg",
}
DOCUMENT_EXTENSIONS = {".pdf", ".docx"}
VISUAL_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".pdf"}
SAFE_ARTIFACT_EXTENSIONS = TEXT_EXTENSIONS | DOCUMENT_EXTENSIONS | VISUAL_EXTENSIONS


def _within(root: Path, relative: str) -> Path:
    rel = Path(relative)
    if rel.is_absolute() or any(part in {"..", ""} for part in rel.parts):
        raise ValueError("artifact path must be a safe repository-relative path")
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("artifact path escapes workspace") from exc
    if candidate.is_symlink():
        raise ValueError("symlink artifacts are not supported")
    return candidate


def artifact_file(root: Path, relative: str, *, max_bytes: int = 8 * 1024 * 1024) -> dict[str, Any]:
    path = _within(root, relative)
    if not path.is_file():
        raise FileNotFoundError(relative)
    ext = path.suffix.lower()
    if ext not in SAFE_ARTIFACT_EXTENSIONS:
        raise ValueError(f"unsupported artifact extension: {ext or '<none>'}")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("artifact is not a regular file")
    if info.st_size > max_bytes:
        raise ValueError(f"artifact is too large to publish: {info.st_size} bytes > {max_bytes}")
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return {
        "absolute_path": str(path),
        "path": str(path.relative_to(root)),
        "name": path.name,
        "extension": ext,
        "size": info.st_size,
        "mime_type": mime,
    }


def _read_text(path: Path, max_chars: int) -> tuple[str, bool]:
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    return text[:max_chars], len(text) > max_chars


def _extract_docx(path: Path, max_chars: int) -> tuple[str, bool]:
    with zipfile.ZipFile(path) as archive:
        raw = archive.read("word/document.xml")
    root = ET.fromstring(raw)
    chunks: list[str] = []
    for node in root.iter():
        if node.tag.endswith("}t") and node.text:
            chunks.append(node.text)
        elif node.tag.endswith("}p"):
            chunks.append("\n")
    text = "".join(chunks).strip()
    return text[:max_chars], len(text) > max_chars


def _extract_pdf(path: Path, max_chars: int, max_pages: int = 20) -> tuple[str, bool, int]:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        return "", False, 0
    reader = PdfReader(str(path))
    chunks: list[str] = []
    pages = min(len(reader.pages), max_pages)
    for page in reader.pages[:pages]:
        try:
            chunks.append(page.extract_text() or "")
        except Exception:
            chunks.append("")
        if sum(len(c) for c in chunks) >= max_chars:
            break
    text = "\n\n".join(chunks)
    return text[:max_chars], len(text) > max_chars or len(reader.pages) > pages, len(reader.pages)


def _image_preview(path: Path, *, max_base64_chars: int = 9000) -> dict[str, Any] | None:
    try:
        from PIL import Image  # type: ignore
    except Exception:
        return None

    image = None
    if path.suffix.lower() == ".pdf":
        try:
            import fitz  # type: ignore
            document = fitz.open(str(path))
            if not document.page_count:
                return None
            page = document.load_page(0)
            pix = page.get_pixmap(matrix=fitz.Matrix(1.0, 1.0), alpha=False)
            image = Image.open(BytesIO(pix.tobytes("png")))
        except Exception:
            return None
    else:
        try:
            image = Image.open(path)
            try:
                image.seek(0)
            except Exception:
                pass
        except Exception:
            return None

    if image is None:
        return None
    try:
        image = image.convert("RGB")
        original = image.size
        for side, quality in ((640, 50), (480, 45), (320, 40), (224, 35), (160, 30)):
            preview = image.copy()
            preview.thumbnail((side, side))
            out = BytesIO()
            preview.save(out, format="JPEG", quality=quality, optimize=True)
            payload = base64.b64encode(out.getvalue()).decode("ascii")
            if len(payload) <= max_base64_chars:
                return {
                    "mime_type": "image/jpeg",
                    "width": preview.width,
                    "height": preview.height,
                    "source_width": original[0],
                    "source_height": original[1],
                    "bytes": len(out.getvalue()),
                    "base64": payload,
                }
        return None
    finally:
        try:
            image.close()
        except Exception:
            pass


def scan_artifacts(
    root: Path,
    *,
    relative_path: str = ".",
    since_epoch: int = 0,
    changed_only: bool = True,
    max_entries: int = 80,
    max_text_chars: int = 20000,
    max_visual_previews: int = 2,
    total_text_chars: int = 24000,
) -> dict[str, Any]:
    base = root if relative_path in {"", ".", "./"} else _within(root, relative_path)
    if not base.exists():
        raise FileNotFoundError(relative_path)
    iterator = [base] if base.is_file() else base.rglob("*")
    items: list[dict[str, Any]] = []
    preview_count = 0
    text_remaining = max(0, int(total_text_chars))
    for path in iterator:
        if len(items) >= max_entries:
            break
        if not path.is_file() or path.is_symlink():
            continue
        try:
            rel = path.resolve().relative_to(root.resolve())
        except ValueError:
            continue
        if ".git" in rel.parts:
            continue
        ext = path.suffix.lower()
        if ext not in SAFE_ARTIFACT_EXTENSIONS:
            continue
        info = path.stat()
        if changed_only and since_epoch and info.st_mtime + 1 < since_epoch:
            continue
        entry: dict[str, Any] = {
            "path": str(rel),
            "name": path.name,
            "extension": ext,
            "size": info.st_size,
            "modified_at": int(info.st_mtime),
            "mime_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            "kind": "visual" if ext in VISUAL_EXTENSIONS else ("document" if ext in DOCUMENT_EXTENSIONS else "text"),
        }
        try:
            if ext in TEXT_EXTENSIONS and info.st_size <= 2 * 1024 * 1024 and text_remaining > 0:
                cap = min(max_text_chars, text_remaining)
                content, truncated = _read_text(path, cap)
                entry["content"] = content
                entry["content_truncated"] = truncated
                text_remaining -= len(content)
                if ext == ".json":
                    try:
                        json.loads(content)
                        entry["structured"] = True
                    except Exception:
                        entry["structured"] = False
            elif ext == ".docx" and info.st_size <= 8 * 1024 * 1024 and text_remaining > 0:
                cap = min(max_text_chars, text_remaining)
                content, truncated = _extract_docx(path, cap)
                entry["content"] = content
                entry["content_truncated"] = truncated
                text_remaining -= len(content)
            elif ext == ".pdf" and info.st_size <= 16 * 1024 * 1024:
                cap = min(max_text_chars, text_remaining) if text_remaining > 0 else 0
                content, truncated, page_count = _extract_pdf(path, cap) if cap else ("", False, 0)
                entry["page_count"] = page_count
                if content:
                    entry["content"] = content
                    entry["content_truncated"] = truncated
                    text_remaining -= len(content)
        except Exception as exc:
            entry["extract_error"] = type(exc).__name__

        if ext in VISUAL_EXTENSIONS and preview_count < max_visual_previews:
            preview = _image_preview(path)
            if preview:
                entry["visual_preview"] = preview
                preview_count += 1
        items.append(entry)
    return {
        "path": relative_path,
        "changed_only": changed_only,
        "since_epoch": since_epoch,
        "items": items,
        "truncated": len(items) >= max_entries,
        "visual_previews": preview_count,
        "text_budget_remaining": text_remaining,
    }
