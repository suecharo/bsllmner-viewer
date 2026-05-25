from collections import defaultdict, deque

from hypothesis import given
from hypothesis import strategies as st

from bsllmner_viewer.etl.build_ontology import _compute_depths


def _ref_depth(parents: dict[str, set[str]], all_terms: set[str]) -> dict[str, int]:
    """参照実装: parents をたどって各 term の root からの最短距離を返す。

    root = parents が空、または all_terms に含まれない親しか持たない term。
    複数 root の場合は min(distance)。到達不能 term は depth=0 で fallback。
    """
    children: dict[str, set[str]] = defaultdict(set)
    for term, ps in parents.items():
        for parent in ps:
            children[parent].add(term)
    roots = [
        t
        for t in all_terms
        if not parents.get(t) or all(p not in all_terms for p in parents[t])
    ]
    depth: dict[str, int] = {r: 0 for r in roots}
    queue: deque[str] = deque(roots)
    while queue:
        node = queue.popleft()
        d = depth[node]
        for child in children.get(node, ()):
            if child not in all_terms:
                continue
            new_d = d + 1
            if child not in depth or depth[child] > new_d:
                depth[child] = new_d
                queue.append(child)
    for term in all_terms:
        depth.setdefault(term, 0)

    return depth


@st.composite
def _dag(draw: st.DrawFn) -> tuple[dict[str, set[str]], set[str]]:
    n = draw(st.integers(min_value=1, max_value=8))
    terms = [f"T:{i}" for i in range(n)]
    parents: dict[str, set[str]] = {}
    for i, term in enumerate(terms):
        # 親はインデックスが小さい term のみ → サイクルなし
        possible_parents = terms[:i]
        if possible_parents:
            chosen = draw(
                st.lists(
                    st.sampled_from(possible_parents),
                    max_size=min(3, i),
                    unique=True,
                )
            )
        else:
            chosen = []
        if chosen:
            parents[term] = set(chosen)

    return parents, set(terms)


@given(_dag())
def test_compute_depths_matches_reference_bfs(
    pair: tuple[dict[str, set[str]], set[str]]
) -> None:
    parents, all_terms = pair
    expected = _ref_depth(parents, all_terms)
    actual = _compute_depths(parents, all_terms)
    assert actual == expected
