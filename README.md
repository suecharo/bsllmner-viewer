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

開発はすべて Docker Compose 内で実行する。ホストに Python・uv は不要 (ontology subset の生成だけ host で Docker を叩く)。

前提:

- bsllmner-mk2 の select 結果 (`select_*.json` + `bs_entries_*.jsonl`) を `data/{chip-atlas-hg38,chip-atlas-mm10,rnaseq-human,rnaseq-mouse}/` 配下に配置済み (構成は [data/README.md](data/README.md) 参照)
- host に Docker (`obolibrary/robot:latest` を pull 可能) と git。subset 生成時のみ使う

起動:

```bash
cp env.dev .env
docker compose up -d --build
docker compose exec app uv sync
```

`http://localhost:8000` で Streamlit が応答する (parquet が未生成なら起動はするが画面はほぼ空)。

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

### 0a. Ontology 用意 (`build-ontology` の前に必須)

`ontology.parquet` は viewer 自身が用意した OWL から組み立てる。2 段階:

```bash
# upstream OWL を data/ontology/ に DL (~1.3 GB、初回のみ)
docker compose exec app uv run python scripts/fetch_ontology_owls.py

# subset OWL を生成 (host で実行、obolibrary/robot:latest を docker run で呼ぶ)
bash scripts/build_ontology_subsets.sh           # 不足分のみ生成
bash scripts/build_ontology_subsets.sh --force   # 強制再生成
```

`build_ontology_subsets.sh` は host での Docker / git / gawk が必要。`chebi_subset.owl` の生成は ROBOT に 24 GB Java heap (`-Xmx24g`) を渡すので host RAM 24 GB 以上を要する。詳細は [docs/etl.md](docs/etl.md) §「Ontology OWL」を参照。

### 0b. 事前 cache (`build-srx-links` の前に必須)

`build-srx-links` は NCBI `SRA_Accessions.tab` (~32 GB) と ChIP-Atlas `experimentList.tab` (~340 MB) を読む。download は ETL からは走らせず、専用 script で事前 fetch する (`scripts/_http_cache.py` の resume / ETag 判定付き):

```bash
# ~32 GB。初回のみ数時間。再実行時は ETag / Last-Modified 差分があれば resume / 再 download
docker compose exec app uv run python scripts/fetch_sra_accessions.py

# ~340 MB。週次以上で更新があるので月 1 で --force 再 fetch 推奨
docker compose exec app uv run python scripts/fetch_chip_atlas_experiment_list.py
```

出力先: `${BSLLMNER_VIEWER_DATA_DIR}/cache/{SRA_Accessions.tab,experimentList.tab}` (sidecar `.meta.json` 付き)。cache 不在のまま `build-srx-links` を走らせると `srx_links.parquet` が空になり、`samples.sequence_type` も系統 default まで退行する。

### 1. parquet 生成

```bash
# 全部生成 (runs → samples → facts → ontology → srx-links → aggregates)
docker compose exec app uv run python -m bsllmner_viewer.etl build-all

# 個別実行 (デバッグ・部分再生成、依存順は build-all と同じ)
docker compose exec app uv run python -m bsllmner_viewer.etl build-runs
docker compose exec app uv run python -m bsllmner_viewer.etl build-samples -s rnaseq-human
docker compose exec app uv run python -m bsllmner_viewer.etl build-facts -s chip-atlas-hg38
docker compose exec app uv run python -m bsllmner_viewer.etl build-ontology
docker compose exec app uv run python -m bsllmner_viewer.etl build-srx-links
docker compose exec app uv run python -m bsllmner_viewer.etl build-aggregates
```

出力先 default: `${BSLLMNER_VIEWER_DATA_DIR}/parquet/{runs,samples,facts,ontology,srx_links,agg_samples_by_dims,agg_field_term_dims,agg_field_status_dims}.parquet`。

`-s/--source-system` は複数指定可 (`-s chip-atlas-hg38 -s chip-atlas-mm10`)。省略時は全系統。

### 規模感 (現状の data/)

| parquet | 行数 | 内訳 |
|---|---|---|
| `runs.parquet` | 311 | chip-atlas-hg38 7 + chip-atlas-mm10 7 + rnaseq-human 148 + rnaseq-mouse 149 (= 月次 chunk 数) |
| `samples.parquet` | 4,189,635 | chip-atlas-hg38 179,015 + chip-atlas-mm10 188,122 + rnaseq-human 2,098,024 + rnaseq-mouse 1,724,474 |
| `facts.parquet` | 33,943,940 | 約 samples × 8 fields。extract/mapping 失敗行も含む |
| `ontology.parquet` | 5,438,310 | ChEBI 4,854,392 + MONDO 246,740 + Cellosaurus 167,127 + UBERON 133,614 + CL 36,437 (transitive closure 込み) |
| `srx_links.parquet` | 4,683,369 | BioSample → SRX の 1:N 展開。`sequence_type` は ChIP-Atlas `experimentList.tab` 由来 |
| `agg_samples_by_dims.parquet` | 323 | (year, source_system, sequence_type, organism) の事前集計。Home / Curation の cold start 用 |
| `agg_field_term_dims.parquet` | 42,306 | field × term × dims の sample_count 上位 200 |
| `agg_field_status_dims.parquet` | 5,383 | field × extract_status × dims の集計 |

## ドキュメント

| ファイル | 内容 |
|---|---|
| [data-model.md](docs/data-model.md) | parquet schema (samples / facts / runs / ontology / srx_links / 集計 3 種)、ontology_source 命名規約、extract_status 決定ロジック、sequence_type 取扱 |
| [etl.md](docs/etl.md) | 入力 source、normalize、streaming、CLI subcommand、cache fetch、出力 path、エラー方針、test 構成 |
| [ui.md](docs/ui.md) | Streamlit UI (Home / Curation / Gap Discovery / Cohort / Gapminder) の責務、操作要素、lib/ 共通層 API |
| [data/README.md](data/README.md) | data/ 配下の bsllmner-mk2 出力 / 入力 BS の構成・出典・収集経路 |

## ライセンス

Apache-2.0 (`LICENSE`)。
