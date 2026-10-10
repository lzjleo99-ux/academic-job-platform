"""Raw payload object store.

Key layout (identical for a local directory and an S3-style bucket; keys never start with '/'):

    {raw_<kind>}/v1/{source_id}/{yyyy}/{mm}/{dd}/{sha256}.{ext}.gz       payload, gzip
    {raw_<kind>}/v1/{source_id}/{yyyy}/{mm}/{dd}/{sha256}.meta.json      fetch metadata

* ``kind`` is html | json | xml | pdf | txt; the directory is ``raw_html``, ``raw_json`` ...
* the date is the UTC fetch date and sha256 is of the *uncompressed* payload, so refetching
  identical bytes on the same day is idempotent while a later refetch keeps its own history.
* the sidecar is the provenance envelope: source_id, url, fetched_at, http_status, robots_allowed
  (ingestion refuses rows whose envelope is missing or robots_allowed is not true).
* ``listing.raw_html_ref`` stores the payload key; the sidecar key is derived by ``meta_key``.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Protocol, Tuple

LAYOUT_VERSION = "v1"
KINDS = {"html": "html", "json": "json", "xml": "xml", "pdf": "pdf", "txt": "txt",
        "docx": "docx", "doc": "doc"}   # T31 (packet 019): board.py's own document-linked konkurs rows
_SOURCE_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...


class LocalFsStore:
    def __init__(self, root: "os.PathLike[str] | str"):
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root != p and self.root not in p.parents:
            raise ValueError(f"key escapes store root: {key!r}")
        return p

    def put(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + f".tmp{os.getpid()}")
        tmp.write_bytes(data)
        os.replace(tmp, p)  # atomic on POSIX

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()


def default_store() -> LocalFsStore:
    return LocalFsStore(os.environ.get("RAW_STORE_ROOT", "data/raw"))


@dataclass(frozen=True)
class RawRef:
    key: str
    sha256: str
    size: int


def _base(kind: str, source_id: str, fetched_at: datetime, sha256: str) -> str:
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    if not _SOURCE_RE.match(source_id):
        raise ValueError(f"bad source_id {source_id!r}")
    d = fetched_at.astimezone(timezone.utc)
    return f"raw_{kind}/{LAYOUT_VERSION}/{source_id}/{d:%Y}/{d:%m}/{d:%d}/{sha256}"


def meta_key(payload_key: str) -> str:
    return payload_key.rsplit(".", 2)[0] + ".meta.json"


def put_raw(
    store: ObjectStore,
    *,
    kind: str,
    source_id: str,
    url: str,
    content: bytes,
    fetched_at: Optional[datetime] = None,
    status: Optional[int] = None,
    content_type: Optional[str] = None,
    final_url: Optional[str] = None,
    robots_allowed: Optional[bool] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> RawRef:
    fetched_at = fetched_at or datetime.now(timezone.utc)
    if fetched_at.tzinfo is None:
        raise ValueError("fetched_at must be timezone-aware")
    sha = hashlib.sha256(content).hexdigest()
    key = f"{_base(kind, source_id, fetched_at, sha)}.{KINDS[kind]}.gz"
    if not store.exists(key):
        # mtime=0 keeps the gzip bytes deterministic for identical content
        store.put(key, gzip.compress(content, mtime=0))
    meta = {
        "source_id": source_id,
        "robots_allowed": robots_allowed,
        "url": url,
        "final_url": final_url or url,
        "fetched_at": fetched_at.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "http_status": status,
        "content_type": content_type,
        "sha256": sha,
        "size": len(content),
        "layout": LAYOUT_VERSION,
        **({"extra": extra} if extra else {}),
    }
    store.put(meta_key(key), json.dumps(meta, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    return RawRef(key=key, sha256=sha, size=len(content))


def get_raw(store: ObjectStore, key: str) -> Tuple[bytes, Dict[str, Any]]:
    content = gzip.decompress(store.get(key))
    meta = json.loads(store.get(meta_key(key)))
    if hashlib.sha256(content).hexdigest() != meta["sha256"]:
        raise ValueError(f"checksum mismatch for {key}")
    return content, meta
