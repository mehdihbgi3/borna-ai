from __future__ import annotations

import gzip
import hashlib
import os
import re
import tempfile
from pathlib import Path

_REL = re.compile(r"[0-9a-f]{2}/[0-9a-f]{64}\.(html|json|png|jpg|webp|pdf|txt)\.gz")
MIME = {"html": "text/html", "json": "application/json", "png": "image/png", "jpg": "image/jpeg",
        "webp": "image/webp", "pdf": "application/pdf", "txt": "text/plain"}


def sniff_upload(data: bytes, filename: str = "") -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith(b"%PDF-"):
        return "pdf"
    if filename.lower().endswith(".txt"):
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return None
        return "txt"
    return None


class EvidenceStore:

    def __init__(self, root):
        self.root = Path(root)

    def put(self, data: bytes, ext: str) -> tuple[str, str, int]:
        if ext not in MIME:
            raise ValueError("نوع فایل مدرک مجاز نیست.")
        sha = hashlib.sha256(data).hexdigest()
        rel = f"{sha[:2]}/{sha}.{ext}.gz"
        path = self.root / rel
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as fh, gzip.GzipFile(fileobj=fh, mode="wb", mtime=0) as gz:
                    gz.write(data)
                os.replace(tmp, path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
        return sha, rel, len(data)

    def path(self, rel: str) -> Path:
        if not _REL.fullmatch(rel or ""):
            raise ValueError("نشانی مدرک نامعتبر است.")
        return self.root / rel

    def read(self, rel: str) -> bytes:
        with gzip.open(self.path(rel), "rb") as fh:
            return fh.read()

    def verify(self, rel: str, sha: str) -> bool:
        try:
            return hashlib.sha256(self.read(rel)).hexdigest() == sha
        except (OSError, ValueError):
            return False

    @staticmethod
    def ext_of(rel: str) -> str:
        return rel.rsplit(".", 2)[-2]
