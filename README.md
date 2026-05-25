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

前提:

- bsllmner-mk2 の `ontology/` ディレクトリがホストに存在する (ontology.parquet 生成に必要、ro bind mount)
- `env.dev` の `BSLLMNER_VIEWER_BSLLMNER_MK2_ONTOLOGY_DIR` をそのホスト path に合わせる

起動:

```bash
cp env.dev .env
docker compose up -d --build
docker compose exec app uv sync
```

`http://localhost:8000` で Streamlit が応答する。

`env.dev` を編集したら `cp env.dev .env` を再実行 + `docker compose up -d --force-recreate`。

よく使うコマンド:

```bash
docker compose exec app uv run pytest
docker compose exec app uv run ruff check
docker compose exec app uv run ruff format
docker compose exec app uv run mypy src
```

## ETL: parquet 生成

詳細仕様は [docs/etl.md](docs/etl.md)、出力スキーマは [docs/data-model.md](docs/data-model.md)。

```bash
# 全部生成 (runs → samples → facts → ontology)
docker compose exec app uv run python -m bsllmner_viewer.etl build-all

# 個別実行 (デバッグ・部分再生成)
docker compose exec app uv run python -m bsllmner_viewer.etl build-runs
docker compose exec app uv run python -m bsllmner_viewer.etl build-samples -s rnaseq-human
docker compose exec app uv run python -m bsllmner_viewer.etl build-facts -s chip-atlas-hg38
docker compose exec app uv run python -m bsllmner_viewer.etl build-ontology --ontology-source MONDO
```

出力先 default: `${BSLLMNER_VIEWER_DATA_DIR}/parquet/{runs,samples,facts,ontology}.parquet`。

### 規模感 (現状の data/)

| parquet | 件数オーダー | 備考 |
|---|---|---|
| `runs.parquet` | 62 runs | chip-atlas 2 + rnaseq 60 月 |
| `samples.parquet` | 約 1.66M rows | rnaseq 1.29M + chip-atlas hg38 179K + mm10 188K |
| `facts.parquet` | 1.66M × 8 fields = 約 13M rows (推定) | extract/mapping 失敗行も含む |
| `ontology.parquet` | (要確認) | rdflib 移行で hierarchy 復活後に再見積もり |

## Known issues (PoC)

- **ontology.parquet の hierarchy が抜けている**: 現状 owlready2 ベースの parse で subset OWL の `is_a` を辿れず、self-loop のみで `parent_term_id != term_id` の行が 0 件。rdflib で `rdfs:subClassOf` を直接 query する方針に切り替え予定 (Phase B 課題)。
- **samples.organism の表記揺れ**: 同じ Homo sapiens でも `Homo Sapiens` / `Human` / `Homo sapiens/Mus musculus xenograft` 等 7 種類が検出された。正規化方針は別途検討。

## ドキュメント

| ファイル | 内容 |
|---|---|
| [data-model.md](docs/data-model.md) | 5 parquet schema (samples / facts / runs / ontology / chip_atlas_links)、ontology_source 命名規約、extract_status 決定ロジック |
| [etl.md](docs/etl.md) | 入力 source、normalize、streaming、CLI subcommand、出力 path、エラー方針、test 構成 |

## ライセンス

Apache-2.0 (`LICENSE`)。
