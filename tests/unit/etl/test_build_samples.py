"""build_samples._publication_year の挙動検証。

- ``publication_date`` が None → ``submission_year=None``
- ``year <= ETL 実行年`` はそのまま採用
- ``year > ETL 実行年`` は ``None`` (embargo 解除予定日由来の future date 除外)
"""

from __future__ import annotations

import datetime

import hypothesis.strategies as st
from hypothesis import given

from bsllmner_viewer.etl.build_samples import _publication_year

_NOW = datetime.datetime(2026, 6, 15, tzinfo=datetime.UTC)


def test_none_publication_date_returns_none() -> None:
    assert _publication_year(None, now=_NOW) is None


def test_past_year_returns_year() -> None:
    pub = datetime.datetime(2024, 3, 1, tzinfo=datetime.UTC)
    assert _publication_year(pub, now=_NOW) == 2024


def test_current_year_returns_year() -> None:
    pub = datetime.datetime(2026, 12, 31, 23, 59, tzinfo=datetime.UTC)
    assert _publication_year(pub, now=_NOW) == 2026


def test_next_year_returns_none() -> None:
    pub = datetime.datetime(2027, 1, 1, tzinfo=datetime.UTC)
    assert _publication_year(pub, now=_NOW) is None


def test_far_future_returns_none() -> None:
    pub = datetime.datetime(2099, 6, 1, tzinfo=datetime.UTC)
    assert _publication_year(pub, now=_NOW) is None


def test_naive_publication_date_handled() -> None:
    pub = datetime.datetime(2025, 5, 1)  # naive
    assert _publication_year(pub, now=_NOW) == 2025


def test_default_now_uses_system_clock() -> None:
    pub = datetime.datetime(2010, 1, 1, tzinfo=datetime.UTC)
    assert _publication_year(pub) == 2010


@given(
    pub_year=st.integers(min_value=1900, max_value=3000),
    now_year=st.integers(min_value=2000, max_value=2100),
)
def test_boundary_property(pub_year: int, now_year: int) -> None:
    """year <= now_year ⇔ 結果は year、year > now_year ⇔ 結果は None。"""
    pub = datetime.datetime(pub_year, 6, 15, tzinfo=datetime.UTC)
    now = datetime.datetime(now_year, 6, 15, tzinfo=datetime.UTC)
    result = _publication_year(pub, now=now)
    if pub_year > now_year:
        assert result is None
    else:
        assert result == pub_year
