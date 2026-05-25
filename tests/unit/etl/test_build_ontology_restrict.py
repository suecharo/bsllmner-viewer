from bsllmner_viewer.etl.build_ontology import (
    _ancestors_with_self,
    _compute_depths,
    build_source_rows,
)


def test_build_source_rows_drops_terms_outside_subset() -> None:
    # subset には A, B のみ。フル OWL の hierarchy 上では C → B → A、E → A、D → 孤立。
    # 呼び出し側 (_collect_hierarchy 相当) で child / parent 両方を subset に
    # restrict 済みの parent map を渡す前提なので、ここでは C / E を含む edge は
    # 来ない。subset 外の term (C, E) が row に出ない & A/B の depth が正しいことを
    # 確認する。
    labels: dict[str, str | None] = {
        "MONDO:0000001": "A",
        "MONDO:0000002": "B",
    }
    parents: dict[str, set[str]] = {
        "MONDO:0000002": {"MONDO:0000001"},
    }

    rows = build_source_rows("MONDO", labels, parents)

    term_ids = {r["term_id"] for r in rows}
    parent_ids = {r["parent_term_id"] for r in rows}
    assert term_ids == {"MONDO:0000001", "MONDO:0000002"}
    assert parent_ids == {"MONDO:0000001", "MONDO:0000002"}

    rows_by_term: dict[str, list[dict[str, object]]] = {}
    for r in rows:
        rows_by_term.setdefault(r["term_id"], []).append(r)  # type: ignore[arg-type]

    # A は root → self のみ
    a_rows = rows_by_term["MONDO:0000001"]
    assert len(a_rows) == 1
    assert a_rows[0]["parent_term_id"] == "MONDO:0000001"
    assert a_rows[0]["depth"] == 0
    assert a_rows[0]["label"] == "A"

    # B は self + parent A の 2 row、depth=1
    b_rows = rows_by_term["MONDO:0000002"]
    assert {r["parent_term_id"] for r in b_rows} == {
        "MONDO:0000001",
        "MONDO:0000002",
    }
    assert all(r["depth"] == 1 for r in b_rows)

    # 全 row が ontology_source = MONDO
    assert all(r["ontology_source"] == "MONDO" for r in rows)


def test_build_source_rows_transitive_closure_and_depth() -> None:
    # A ← B ← C、A ← D (D は B / C と独立で root から depth=1)
    labels: dict[str, str | None] = {
        "MONDO:0000001": "A",
        "MONDO:0000002": "B",
        "MONDO:0000003": "C",
        "MONDO:0000004": "D",
    }
    parents: dict[str, set[str]] = {
        "MONDO:0000002": {"MONDO:0000001"},
        "MONDO:0000003": {"MONDO:0000002"},
        "MONDO:0000004": {"MONDO:0000001"},
    }

    rows = build_source_rows("MONDO", labels, parents)

    depths = {r["term_id"]: r["depth"] for r in rows}
    assert depths["MONDO:0000001"] == 0
    assert depths["MONDO:0000002"] == 1
    assert depths["MONDO:0000003"] == 2
    assert depths["MONDO:0000004"] == 1

    # C の祖先 closure = {C, B, A}
    c_parents = {
        r["parent_term_id"] for r in rows if r["term_id"] == "MONDO:0000003"
    }
    assert c_parents == {"MONDO:0000001", "MONDO:0000002", "MONDO:0000003"}


def test_build_source_rows_self_loop_for_every_term() -> None:
    labels: dict[str, str | None] = {
        "MONDO:0000001": "A",
        "MONDO:0000002": "B",
    }
    parents: dict[str, set[str]] = {
        "MONDO:0000002": {"MONDO:0000001"},
    }

    rows = build_source_rows("MONDO", labels, parents)

    pairs = {(r["term_id"], r["parent_term_id"]) for r in rows}
    for term in labels:
        assert (term, term) in pairs


def test_ancestors_with_self_handles_diamond() -> None:
    # A ← B, A ← C, B ← D, C ← D の diamond → D の祖先 = {D, B, C, A}
    parents = {
        "B": {"A"},
        "C": {"A"},
        "D": {"B", "C"},
    }
    assert _ancestors_with_self("D", parents) == {"A", "B", "C", "D"}


def test_compute_depths_with_multiple_roots_uses_min_distance() -> None:
    # A と B が両方 root、C は A の子、D は C の子かつ B の子 → D の depth は
    # min(via A: 2, via B: 1) = 1
    parents = {
        "C": {"A"},
        "D": {"C", "B"},
    }
    all_terms = {"A", "B", "C", "D"}
    depth = _compute_depths(parents, all_terms)
    assert depth == {"A": 0, "B": 0, "C": 1, "D": 1}
