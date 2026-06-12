"""Ontology の upstream OWL を `${BSLLMNER_VIEWER_ONTOLOGY_DIR}/` に download する。

`build-ontology` の前段に必要な OWL を揃える用。subset OWL の生成は別 script
(`scripts/build_ontology_subsets.sh`) で行う。本 script は upstream raw OWL の
DL だけを担当する。

DL 対象:

- ``cellosaurus.obo`` — Cellosaurus (後段で ROBOT convert して ``cellosaurus.owl`` 化)
- ``cl.owl`` — Cell Ontology (subset 生成 + hierarchy 両方の source)
- ``efo.owl`` — Experimental Factor Ontology (CL subset に EFO の細胞 term を merge する用)
- ``uberon.owl`` — UBERON (subset 生成 + hierarchy 両方の source)
- ``mondo.owl`` — MONDO (subset 生成 + hierarchy 両方の source)
- ``chebi.owl`` — ChEBI (subset 生成 + hierarchy 両方の source)

skip 判定は size + ETag / Last-Modified の二段構え (``scripts/_http_cache.py:CachedDownload``
と同じ流儀)。途中で中断された場合は ``.partial`` から HTTP Range で resume。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from _http_cache import download_with_resume

logger = logging.getLogger("fetch_ontology_owls")

_DOWNLOADS: tuple[tuple[str, str], ...] = (
    (
        "cellosaurus.obo",
        "https://ftp.expasy.org/databases/cellosaurus/cellosaurus.obo",
    ),
    (
        "cl.owl",
        "https://purl.obolibrary.org/obo/cl.owl",
    ),
    (
        "efo.owl",
        "https://github.com/EBISPOT/efo/releases/download/current/efo.owl",
    ),
    (
        "uberon.owl",
        "https://purl.obolibrary.org/obo/uberon.owl",
    ),
    (
        "mondo.owl",
        "https://purl.obolibrary.org/obo/mondo.owl",
    ),
    (
        "chebi.owl",
        "https://ftp.ebi.ac.uk/pub/databases/chebi/ontology/chebi.owl",
    ),
)


def _default_ontology_dir() -> Path:
    explicit = os.environ.get("BSLLMNER_VIEWER_ONTOLOGY_DIR")
    if explicit:
        return Path(explicit)
    data_dir = Path(os.environ.get("BSLLMNER_VIEWER_DATA_DIR", "/app/data"))
    return data_dir / "ontology"


def fetch_ontology_owls(out_dir: Path, force: bool = False) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for file_name, url in _DOWNLOADS:
        dest = out_dir / file_name
        logger.info("=== %s ===", file_name)
        written.append(
            download_with_resume(
                url,
                dest,
                force=force,
                progress_interval=64 * 1024**2,
                unit_divisor=1024**2,
                unit_label="MiB",
            )
        )

    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=_default_ontology_dir(),
        help="Output directory (default: $BSLLMNER_VIEWER_ONTOLOGY_DIR "
        "or $BSLLMNER_VIEWER_DATA_DIR/ontology)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if size + etag already match the remote",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    fetch_ontology_owls(args.out_dir, force=args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
