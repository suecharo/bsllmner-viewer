# bsllmner-viewer

bsllmner-mk2 が生成した BioSample × ontology マッピング結果を可視化・探索するための Web UI と、その背後のデータストア・ETL。ローカル demo を前提とした PoC。

## 技術スタック

| 層 | 採用 |
|---|---|
| 言語 / パッケージ管理 | Python 3.12 / uv |
| データストア | DuckDB + Parquet (in-process) |
| UI | Streamlit + Plotly |
| Curation レポート | Jupyter / Quarto |
| テスト | pytest + hypothesis (PBT) |
| Lint / 型 | ruff / mypy |
| 開発環境 | Docker Compose |

## クイックスタート (dev)

開発はすべて Docker Compose 内で実行する。ホストに Python・uv は不要。

```bash
cp env.dev .env
docker compose up -d --build
docker compose exec app uv sync
```

`http://localhost:8501` で Streamlit が応答する。

よく使うコマンド:

```bash
docker compose exec app uv run pytest
docker compose exec app uv run ruff check
docker compose exec app uv run ruff format
docker compose exec app uv run mypy src
```

## ライセンス

Apache-2.0 (`LICENSE`)。
