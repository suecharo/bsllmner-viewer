import logging
import os
from pathlib import Path
from typing import Annotated, cast, get_args

import typer

from bsllmner_viewer.etl.build_facts import build_facts
from bsllmner_viewer.etl.build_ontology import build_ontology
from bsllmner_viewer.etl.build_runs import build_runs
from bsllmner_viewer.etl.build_samples import build_samples
from bsllmner_viewer.etl.types import SourceSystemId

app = typer.Typer(add_completion=False, no_args_is_help=True)

_VALID_SOURCE_SYSTEMS: frozenset[str] = frozenset(get_args(SourceSystemId))


def _setup_logging() -> None:
    level_name = os.environ.get("BSLLMNER_VIEWER_LOG_LEVEL", "info").upper()
    logging.basicConfig(
        level=getattr(logging, level_name, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _default_data_dir() -> Path:
    return Path(os.environ.get("BSLLMNER_VIEWER_DATA_DIR", "/app/data"))


def _default_ontology_dir() -> Path:
    return Path(
        os.environ.get("BSLLMNER_VIEWER_ONTOLOGY_DIR", "/opt/bsllmner-mk2/ontology")
    )


def _default_out_dir() -> Path:
    return _default_data_dir() / "parquet"


SourceOption = Annotated[
    list[str] | None,
    typer.Option(
        "--source-system",
        "-s",
        help="絞り込む系統 (省略時は全系統)。複数指定可。",
    ),
]


def _source_tuple(values: list[str] | None) -> tuple[SourceSystemId, ...] | None:
    if not values:
        return None
    for v in values:
        if v not in _VALID_SOURCE_SYSTEMS:
            raise typer.BadParameter(
                f"unknown --source-system: {v} (valid: {sorted(_VALID_SOURCE_SYSTEMS)})"
            )

    return tuple(cast(SourceSystemId, v) for v in values)


@app.command("build-runs")
def cmd_build_runs(
    data_dir: Annotated[Path, typer.Option("--data-dir", "-d")] = _default_data_dir(),
    out_dir: Annotated[Path, typer.Option("--out-dir", "-o")] = _default_out_dir(),
    source_systems: SourceOption = None,
) -> None:
    """run_metadata + error_count を runs.parquet に集約する。"""
    _setup_logging()
    build_runs(data_dir, out_dir / "runs.parquet", _source_tuple(source_systems))


@app.command("build-samples")
def cmd_build_samples(
    data_dir: Annotated[Path, typer.Option("--data-dir", "-d")] = _default_data_dir(),
    out_dir: Annotated[Path, typer.Option("--out-dir", "-o")] = _default_out_dir(),
    source_systems: SourceOption = None,
) -> None:
    """input JSONL + SelectResult から samples.parquet を生成する。"""
    _setup_logging()
    build_samples(data_dir, out_dir / "samples.parquet", _source_tuple(source_systems))


@app.command("build-facts")
def cmd_build_facts(
    data_dir: Annotated[Path, typer.Option("--data-dir", "-d")] = _default_data_dir(),
    out_dir: Annotated[Path, typer.Option("--out-dir", "-o")] = _default_out_dir(),
    source_systems: SourceOption = None,
) -> None:
    """SelectResult.entries を long format に展開し facts.parquet を生成する。"""
    _setup_logging()
    build_facts(data_dir, out_dir / "facts.parquet", _source_tuple(source_systems))


@app.command("build-ontology")
def cmd_build_ontology(
    ontology_dir: Annotated[
        Path, typer.Option("--ontology-dir")
    ] = _default_ontology_dir(),
    out_dir: Annotated[Path, typer.Option("--out-dir", "-o")] = _default_out_dir(),
    sources: Annotated[
        list[str] | None, typer.Option("--ontology-source", help="例: MONDO, CL, ChEBI")
    ] = None,
) -> None:
    """対象 OWL を parse し ontology.parquet を生成する。"""
    _setup_logging()
    build_ontology(
        ontology_dir,
        out_dir / "ontology.parquet",
        tuple(sources) if sources else None,
    )


@app.command("build-all")
def cmd_build_all(
    data_dir: Annotated[Path, typer.Option("--data-dir", "-d")] = _default_data_dir(),
    ontology_dir: Annotated[
        Path, typer.Option("--ontology-dir")
    ] = _default_ontology_dir(),
    out_dir: Annotated[Path, typer.Option("--out-dir", "-o")] = _default_out_dir(),
    source_systems: SourceOption = None,
) -> None:
    """runs → samples → facts → ontology を依存順で全部生成する。"""
    _setup_logging()
    source_tuple = _source_tuple(source_systems)
    build_runs(data_dir, out_dir / "runs.parquet", source_tuple)
    build_samples(data_dir, out_dir / "samples.parquet", source_tuple)
    build_facts(data_dir, out_dir / "facts.parquet", source_tuple)
    build_ontology(ontology_dir, out_dir / "ontology.parquet", None)


if __name__ == "__main__":
    app()
