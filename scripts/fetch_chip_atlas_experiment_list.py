"""ChIP-Atlas `experimentList.tab` を `${BSLLMNER_VIEWER_DATA_DIR}/cache/` に download する。

ETL の `build-srx-links` は本 script の出力を読んで per-SRX の sequence_type
(``track_type_class``) を確定する。~190 MB の TSV を ETL 内で download せず
事前 fetch する分離は ``fetch_sra_accessions.py`` と同じポリシー。

skip 判定は size + ETag / Last-Modified の二段構え (``_http_cache.py`` 経由)。
ChIP-Atlas は週次以上で SRX 追加 / track_type_class 修正があるため、月次など
定期的に ``--force`` で再 download することを推奨。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from scripts._http_cache import download_with_resume

logger = logging.getLogger("fetch_chip_atlas_experiment_list")

URL = "https://chip-atlas.dbcls.jp/data/metadata/experimentList.tab"


def _default_cache_path() -> Path:
    data_dir = Path(os.environ.get("BSLLMNER_VIEWER_DATA_DIR", "/app/data"))
    return data_dir / "cache" / "experimentList.tab"


def fetch_chip_atlas_experiment_list(out: Path, force: bool = False) -> Path:
    return download_with_resume(
        URL,
        out,
        force=force,
        progress_interval=64 * 1024**2,
        unit_divisor=1024**2,
        unit_label="MiB",
    )


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
        help="Re-download even if size + etag already match the remote",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    fetch_chip_atlas_experiment_list(args.out, force=args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
