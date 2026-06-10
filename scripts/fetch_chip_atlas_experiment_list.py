"""ChIP-Atlas `experimentList.tab` を `${BSLLMNER_VIEWER_DATA_DIR}/cache/` に download する。

ETL の `build-srx-links` は本 script の出力を読んで per-SRX の sequence_type
(``track_type_class``) を確定する。~190 MB の TSV を ETL 内で download せず
事前 fetch する分離は ``fetch_sra_accessions.py`` と同じポリシー。

skip 判定: cache の size と HTTP ``Content-Length`` が一致したら download しない
(``--force`` で強制)。中断時は ``.partial`` から HTTP Range で resume する。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import urllib.request
from pathlib import Path

logger = logging.getLogger("fetch_chip_atlas_experiment_list")

URL = "https://chip-atlas.dbcls.jp/data/metadata/experimentList.tab"
CHUNK_SIZE = 8 * 1024 * 1024
PROGRESS_INTERVAL = 64 * 1024**2  # 64 MiB; TSV は 200MB 級


def _default_cache_path() -> Path:
    data_dir = Path(os.environ.get("BSLLMNER_VIEWER_DATA_DIR", "/app/data"))
    return data_dir / "cache" / "experimentList.tab"


def _remote_content_length(url: str) -> int:
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req) as resp:
        return int(resp.headers["Content-Length"])


def fetch_chip_atlas_experiment_list(out: Path, force: bool = False) -> Path:
    remote_size = _remote_content_length(URL)
    if not force and out.exists() and out.stat().st_size == remote_size:
        logger.info("cache up-to-date (%.1f MiB), skipping", remote_size / 1024**2)
        return out

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".partial")
    written = tmp.stat().st_size if tmp.exists() and not force else 0
    if force and tmp.exists():
        tmp.unlink()

    if written and written < remote_size:
        req = urllib.request.Request(URL, headers={"Range": f"bytes={written}-"})
        mode = "ab"
        logger.info("resuming download from %.1f MiB", written / 1024**2)
    else:
        if tmp.exists():
            tmp.unlink()
        written = 0
        req = urllib.request.Request(URL)
        mode = "wb"
        logger.info("starting fresh download (%.1f MiB)", remote_size / 1024**2)

    next_progress = (written // PROGRESS_INTERVAL + 1) * PROGRESS_INTERVAL
    with urllib.request.urlopen(req) as resp, tmp.open(mode) as f:
        while chunk := resp.read(CHUNK_SIZE):
            f.write(chunk)
            written += len(chunk)
            if written >= next_progress:
                pct = 100.0 * written / remote_size
                logger.info(
                    "downloaded %.1f / %.1f MiB (%.1f%%)",
                    written / 1024**2,
                    remote_size / 1024**2,
                    pct,
                )
                next_progress += PROGRESS_INTERVAL

    if written != remote_size:
        raise RuntimeError(
            f"size mismatch after download: got {written}, expected {remote_size}"
        )
    tmp.replace(out)
    logger.info("done: %s (%.1f MiB)", out, out.stat().st_size / 1024**2)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=_default_cache_path(),
        help="Output path (default: $BSLLMNER_VIEWER_DATA_DIR/cache/experimentList.tab)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if size already matches the remote",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    fetch_chip_atlas_experiment_list(args.out, force=args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
