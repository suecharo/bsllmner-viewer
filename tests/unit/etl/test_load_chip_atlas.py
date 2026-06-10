from __future__ import annotations

from pathlib import Path

from bsllmner_viewer.etl.load_chip_atlas import iter_chip_atlas_experiments


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_iter_chip_atlas_experiments_basic(tmp_path: Path) -> None:
    tab = _write(
        tmp_path / "experimentList.tab",
        "\n".join(
            [
                # SRX, genome, track_type_class, ... (実 file は 9+ 列)
                "SRX001\thg38\tATAC-Seq\tNA\tBlood\tK-562\tdescription\tlog\ttitle",
                "SRX002\tmm10\tHistone\tH3K4me3\tBone\tBM\tdesc\tlog\ttitle\tantibody=H3K4me3",
                "SRX003\thg38\tBisulfite-Seq\tNA\tNA\tNA\tNA\tNA\tNA",
            ]
        )
        + "\n",
    )
    rows = list(iter_chip_atlas_experiments(tab))
    assert len(rows) == 3
    assert rows[0].srx == "SRX001"
    assert rows[0].genome_assembly == "hg38"
    assert rows[0].track_type_class == "ATAC-Seq"
    assert rows[1].srx == "SRX002"
    assert rows[1].track_type_class == "Histone"
    assert rows[2].srx == "SRX003"
    assert rows[2].track_type_class == "Bisulfite-Seq"


def test_iter_chip_atlas_experiments_skips_short_and_empty_lines(tmp_path: Path) -> None:
    tab = _write(
        tmp_path / "exp.tab",
        "\n".join(
            [
                "",  # empty
                "SRX001\thg38",  # too few columns (< 3)
                "\thg38\tATAC-Seq",  # missing SRX
                "SRX002\tmm10\tATAC-Seq",  # ok
            ]
        )
        + "\n",
    )
    rows = list(iter_chip_atlas_experiments(tab))
    assert [r.srx for r in rows] == ["SRX002"]


def test_iter_chip_atlas_experiments_normalizes_na(tmp_path: Path) -> None:
    tab = _write(
        tmp_path / "exp.tab",
        "SRX001\tNA\t\tNA\n" + "SRX002\thg38\tna\n",
    )
    rows = list(iter_chip_atlas_experiments(tab))
    assert len(rows) == 2
    # NA / empty / "na" は normalize で None になる
    assert rows[0].genome_assembly is None
    assert rows[0].track_type_class is None
    assert rows[1].genome_assembly == "hg38"
    assert rows[1].track_type_class is None
