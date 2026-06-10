"""Size + ETag/Last-Modified を sidecar `.meta.json` に保存する HTTP cache helper。

NCBI ``SRA_Accessions.tab`` と ChIP-Atlas ``experimentList.tab`` の事前 download
で共有する。Content-Length 一致だけでは「同じ size で内容が変わった」場合の
stale を検出できないため、HEAD のレスポンスから ``ETag`` / ``Last-Modified``
を持ち帰り、前回保存値と一致するときだけ skip する二段構え (QUA-6)。
"""

from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 8 * 1024 * 1024


@dataclass(frozen=True)
class RemoteMeta:
    size: int
    etag: str | None
    last_modified: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "size": self.size,
            "etag": self.etag,
            "last_modified": self.last_modified,
        }


def remote_meta(url: str) -> RemoteMeta:
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req) as resp:
        return RemoteMeta(
            size=int(resp.headers["Content-Length"]),
            etag=resp.headers.get("ETag"),
            last_modified=resp.headers.get("Last-Modified"),
        )


def _sidecar_path(out: Path) -> Path:
    return out.with_suffix(out.suffix + ".meta.json")


def _read_sidecar(out: Path) -> RemoteMeta | None:
    sidecar = _sidecar_path(out)
    if not sidecar.exists():
        return None
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    size = data.get("size")
    if not isinstance(size, int):
        return None
    return RemoteMeta(
        size=size,
        etag=data.get("etag"),
        last_modified=data.get("last_modified"),
    )


def _write_sidecar(out: Path, meta: RemoteMeta) -> None:
    sidecar = _sidecar_path(out)
    sidecar.write_text(json.dumps(meta.to_dict()), encoding="utf-8")


def is_fresh(out: Path, remote: RemoteMeta) -> bool:
    """Return True if the local cache matches the remote (size + etag/last_mod)."""
    if not out.exists() or out.stat().st_size != remote.size:
        return False
    saved = _read_sidecar(out)
    if saved is None:
        # First-time download: rely on size only. Subsequent runs will be
        # stricter once the sidecar is written.
        return True
    if remote.etag and saved.etag and remote.etag == saved.etag:
        return True
    # Neither ETag nor Last-Modified matched explicitly → return whether
    # Last-Modified matched. If both are missing the caller forces a fresh
    # download.
    return bool(
        remote.last_modified
        and saved.last_modified
        and remote.last_modified == saved.last_modified
    )


def download_with_resume(
    url: str,
    out: Path,
    *,
    force: bool = False,
    progress_interval: int = 64 * 1024**2,
    unit_divisor: int = 1024**2,
    unit_label: str = "MiB",
) -> Path:
    """Download ``url`` to ``out`` with ETag-aware skip + resume.

    The sidecar ``<out>.meta.json`` is updated atomically on success; if the
    download fails partway the ``.partial`` file and sidecar are left in
    place so a subsequent run resumes via HTTP ``Range``.
    """
    remote = remote_meta(url)
    if not force and is_fresh(out, remote):
        logger.info(
            "cache up-to-date (%.1f %s, etag=%s), skipping",
            remote.size / unit_divisor,
            unit_label,
            remote.etag,
        )
        return out

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".partial")
    if force and tmp.exists():
        tmp.unlink()

    written = tmp.stat().st_size if tmp.exists() else 0
    if written and written < remote.size:
        req = urllib.request.Request(url, headers={"Range": f"bytes={written}-"})
        mode = "ab"
        logger.info(
            "resuming download from %.1f %s", written / unit_divisor, unit_label
        )
    else:
        if tmp.exists():
            tmp.unlink()
        written = 0
        req = urllib.request.Request(url)
        mode = "wb"
        logger.info(
            "starting fresh download (%.1f %s)",
            remote.size / unit_divisor,
            unit_label,
        )

    next_progress = (written // progress_interval + 1) * progress_interval
    with urllib.request.urlopen(req) as resp, tmp.open(mode) as f:
        while chunk := resp.read(_CHUNK_SIZE):
            f.write(chunk)
            written += len(chunk)
            if written >= next_progress:
                pct = 100.0 * written / remote.size
                logger.info(
                    "downloaded %.1f / %.1f %s (%.1f%%)",
                    written / unit_divisor,
                    remote.size / unit_divisor,
                    unit_label,
                    pct,
                )
                next_progress += progress_interval

    if written != remote.size:
        raise RuntimeError(
            f"size mismatch after download: got {written}, expected {remote.size}"
        )
    tmp.replace(out)
    _write_sidecar(out, remote)
    logger.info(
        "done: %s (%.1f %s, etag=%s, last_modified=%s)",
        out,
        out.stat().st_size / unit_divisor,
        unit_label,
        remote.etag,
        remote.last_modified,
    )
    return out
