# ETL

bsllmner-mk2 の出力 + OWL → bsllmner-viewer の parquet ファイル群を生成する pipeline の仕様。

スキーマの SSOT は [data-model.md](data-model.md)。本ドキュメントは「どこから何を読んで、どう加工するか」に絞る。

## 入力 source

### 1. bsllmner-mk2 出力 (`${BSLLMNER_VIEWER_DATA_DIR}/`)

3 系統。系統 = (source_system, organism, default_sequence_type) の組。ChIP-Atlas との接続点は `source_system` の prefix で派生する (`source_system LIKE 'chip-atlas-%'`) ので独立した bool 列は持たない。genome (`hg38` / `mm10`) は `lib/chip_atlas.py:SOURCE_SYSTEM_TO_GENOME` の 3 行 dict で導出:

| source_system | organism | default_sequence_type | input | result |
|---|---|---|---|---|
| `chip-atlas-hg38` | Homo sapiens | (per-SRX, `experimentList.tab` 由来) | `data/chip-atlas-hg38/input/bs_entries_hg38.jsonl` (1 file) | `data/chip-atlas-hg38/result/select_*.json` (1 file, 1.2GB) |
| `chip-atlas-mm10` | Mus musculus | (per-SRX, `experimentList.tab` 由来) | `data/chip-atlas-mm10/input/bs_entries_mm10.jsonl` (1 file) | `data/chip-atlas-mm10/result/select_*.json` (1 file, 1.0GB) |
| `rnaseq-human` | Homo sapiens | `RNA-Seq` | `data/rnaseq-human/input/bs_entries_YYYY-MM-01_YYYY-MM-DD.jsonl` (60 files) | `data/rnaseq-human/result/select_rnaseq_*_YYYY-MM.json` (60 files) |

系統定義は `src/bsllmner_viewer/etl/sources.py` に table 駆動で書く（path 解決と (source_system, file) → entries iterator は系統定義から逆引きできる）。

### 2. bsllmner-mk2 OWL (`${BSLLMNER_VIEWER_BSLLMNER_MK2_ONTOLOGY_DIR}/`)

read-only bind mount。各 ontology につき **subset OWL** と **フル OWL** の 2 種類を読む（data-model.md「対象 ontology と source ファイル」参照）:

- subset OWL → `term_id` 集合 + `rdfs:label` の抽出元
- フル OWL → `rdfs:subClassOf` の抽出元（subset OWL からは bsllmner-mk2 の subset 生成時に親参照が剥がされているため）

bsllmner-viewer はこれらを **読むだけ**。生成 (`build_subset_ontologies.sh` 等) は bsllmner-mk2 側の責務。

### 3. NCBI SRA_Accessions (`${BSLLMNER_VIEWER_DATA_DIR}/cache/SRA_Accessions.tab`)

NCBI が提供する全 SRA accession の link 表 (TSV, ~32 GB)。BioSample → SRA Experiment (SRX) のマッピングに使う。

- URL: `https://ftp.ncbi.nlm.nih.gov/sra/reports/Metadata/SRA_Accessions.tab`
- 取得は `scripts/fetch_sra_accessions.py` で **事前 download**。ETL subcommand は cache file を読むだけで、download は走らせない (32 GB の download を ETL 内に持ち込まないため)
- skip 判定: `--force` 指定時を除き、cache の size と HTTP `Content-Length` ヘッダが一致 **かつ** sidecar `.meta.json` に保存した `ETag` / `Last-Modified` がレスポンスヘッダと一致したら download しない。size 一致だけだと内容変更時の cache stale を検出できないため二段構え (`scripts/_http_cache.py:CachedDownload`)
- 列: `Accession` / `Submission` / `Status` / `Updated` / `Published` / `Received` / `Type` / `Center` / `Visibility` / `Alias` / `Experiment` / `Sample` / `Study` / `Loaded` / `Spots` / `Bases` / `Md5sum` / `BioSample` / `BioProject` / `ReplacedBy`
- 使うのは `Accession` / `Type` / `Status` / `Experiment` / `Sample` / `Study` / `BioSample` / `BioProject` のみ

### 4. ChIP-Atlas experimentList.tab (`${BSLLMNER_VIEWER_DATA_DIR}/cache/experimentList.tab`)

ChIP-Atlas が提供する SRX → メタ表 (TSV)。`srx_links.parquet.sequence_type` および `samples.parquet.sequence_type` の唯一の情報源。

- URL: `https://chip-atlas.dbcls.jp/data/metadata/experimentList.tab`
- 取得は `scripts/fetch_chip_atlas_experiment_list.py` で **事前 download**。判定ロジックは `SRA_Accessions.tab` と共通で size + ETag/Last-Modified の二段構え
- 列: `SRX` / `genome` / `track_type_class` / `cell_type_class` / `cell_type` / `antigen_class` / `antigen` / ... (ChIP-Atlas 公式)
- 使うのは `SRX` / `track_type_class` のみ。後者を `etl/seq_type.py:normalize_seq_type` で `ChIP-Seq` / `ATAC-Seq` / `DNase-Seq` / `Bisulfite-Seq` / `ChIP-Seq (input)` / `Annotation track` / `RNA-Seq` のいずれかに正規化
- update 頻度: 週次以上で SRX 追加 / track_type_class 修正がある。月 1 で `--force` 再 fetch するのが推奨運用 (docs/data-model.md sequence_type 取扱節を参照)

## input JSONL の normalize

2 系統で構造差があるため `etl/load_input.py:read_bs_entries(path)` で吸収する。

| 系統 | 構造 | accession path | publication_date path | organism path | title path | bioproject path |
|---|---|---|---|---|---|---|
| chip-atlas (hg38/mm10) | フラット | `.accession` | `.publication_date` | `.Description.Organism.OrganismName` | `.Description.Title` | `.Attributes.Attribute[?attribute_name=='bioproject_id'].content` |
| rnaseq-human | `{"BioSample": {...}}` ラップ | `.BioSample.accession` | `.BioSample.publication_date` | `.BioSample.Description.Organism.OrganismName` | `.BioSample.Description.Title` | `.BioSample.Attributes.Attribute[?attribute_name=='bioproject_id'].content` |

`Attributes.Attribute` は 1 件のとき dict、複数のとき list で来る (bsllmner-mk2 docs §「EBI-style entries」と同じ正規化が要る)。`etl/load_input.py` で常に list 化する。

`publication_date` の年抽出: `datetime.fromisoformat()` で parse して `.year`。`+09:00` のような offset 付きでも parse 可能（Python 3.11+ で `Z` も OK、PoC 想定の 3.12 では問題なし）。

## SelectResult の streaming load

`select_*.json` は 1.2GB に達する単一 JSON。`json.load()` 一発はメモリ的に重い。

→ `ijson` で `entries.item` を逐次 yield する `etl/load_select.py:iter_select_entries(path)` を作る。`run_metadata` / `errors` / `performance` だけは別途 small parse で取得 (`ijson.kvitems(file, '')`)。

Pydantic でスキーマを validate しつつ stream:

```python
for entry in iter_select_entries(path):
    # entry は SelectEntry (Pydantic) 1 件
    ...
```

`run_metadata` を別途取得する関数 `read_select_run_metadata(path)` も用意する（`runs.parquet` 用 + 入力 file → run_name の対応取得）。

## Pydantic types

`src/bsllmner_viewer/etl/types.py` に bsllmner-mk2 docs/data-formats.md のスキーマを写経:

- `RunMetadata`, `ErrorLog`, `LlmTimingFields`
- `ExtractEntry`, `ResolvedValue`, `SearchResult`
- `SelectEntry` (=`{extract, search_results, text2term_results, select_timings, results}`)
- `BsInputEntry` (chip-atlas / rnaseq の normalized 形)

`text2term_results` / `search_results` / `select_timings` は parquet には載せないが、type だけ用意して読み飛ばしやすくする（`= Field(default=None, exclude=True)` で memory 最適化）。

## subcommand 一覧

CLI entrypoint: `src/bsllmner_viewer/etl/cli.py`。`uv run python -m bsllmner_viewer.etl <subcommand>` で呼ぶ。

| subcommand | 出力 | 内容 |
|---|---|---|
| `build-runs` | `runs.parquet` | 全 select_*.json の run_metadata を集約 |
| `build-samples` | `samples.parquet` | input JSONL + select_*.json の accession 集合を join。SRX 列 (`srx_first` / `srx_count` / `sequence_type`) は空に初期化し、`build-srx-links` 側で in-place 上書き。`in_chip_atlas` / `chip_atlas_genome` は samples 列としては持たず、`source_system` から `lib/chip_atlas.py` 経由で派生する (data-model.md 参照) |
| `build-facts` | `facts.parquet` | select_*.json の entries を long format 展開 (extract_status 含む)。`(field, accession, term_id)` 順に sort + `row_group_size=131_072` + `compression='zstd'` + `write_statistics=True` で書き出すことで、UI hot path の `WHERE field=?` 系 query の row group pruning を効かせる |
| `build-ontology` | `ontology.parquet` | subset OWL から term_id + label、フル OWL から subClassOf を `lxml.iterparse` で streaming 抽出 → subset 内に restrict → transitive closure |
| `build-srx-links` | `srx_links.parquet` + samples.parquet 上書き | `SRA_Accessions.tab` を streaming 読みし `Type == "EXPERIMENT"` かつ `BioSample` が samples.parquet の accession 集合に含まれる行を採用。`--experiment-list-tab` 経由で ChIP-Atlas `experimentList.tab` cache を読み、SRX → `track_type_class` を `etl/seq_type.py:normalize_seq_type` で正規化して `srx_links.sequence_type` に inline。**完了後 samples.parquet を読み返し、accession 単位で aggregate して `srx_first` / `srx_count` / `sequence_type` を inline で焼き込む** (per-SRX の値を `combine_seq_types` で BS 単位に集約、tmp parquet → `os.replace` で atomic swap) |
| `build-aggregates` | `agg_samples_by_dims.parquet` / `agg_field_term_dims.parquet` / `agg_field_status_dims.parquet` | UI cold start の 13.3M facts スキャンを 0 にする pre-aggregation。samples + facts の (year, source_system, sequence_type, organism, field, term_id, extract_status) を SUM 集計し、~10〜100K 行の小さい parquet 3 個として書き出す。NULL は `'(unknown)'` sentinel に塗り潰す (詳細は data-model.md sequence_type 取扱節)。`agg_field_term_dims` は field 別に sample_count 上位 200 term のみ |
| `build-all` | 全 parquet | 上記を依存順 (runs → samples → facts → ontology → srx-links → **aggregates**) で実行 |

各 subcommand は `--source-system` で系統を絞れる (省略時は全系統)。`--out-dir` で出力先を override 可能 (default `${BSLLMNER_VIEWER_DATA_DIR}/parquet`)。

`build-srx-links` 専用の option:

- `--source-tab` (`-t`): `SRA_Accessions.tab` の path (default `${BSLLMNER_VIEWER_DATA_DIR}/cache/SRA_Accessions.tab`)
- `--experiment-list-tab`: ChIP-Atlas `experimentList.tab` の path (default `${BSLLMNER_VIEWER_DATA_DIR}/cache/experimentList.tab`)。cache 不在時は SRX → sequence_type マップが空になり、`samples.sequence_type` は systems の `default_sequence_type` (rnaseq-human のみ "RNA-Seq") か `null` のままになる
- `--samples-path`: 参照する samples.parquet (default `${BSLLMNER_VIEWER_DATA_DIR}/parquet/samples.parquet`)。samples.parquet の accession 集合を target にする
- `--source-system` は受け付けるが意味は持たない (samples.parquet 側で既に絞り込まれている前提)

`build-aggregates` 専用の option:

- `--samples-path` / `--facts-path` / `--out-dir`: 各 parquet path の override (default は `${BSLLMNER_VIEWER_DATA_DIR}/parquet/` 配下)
- 必ず `build-samples` / `build-facts` の後に流す。`build-all` が依存順を担保

## 出力 path

`${BSLLMNER_VIEWER_DATA_DIR}/parquet/{samples,facts,runs,ontology}.parquet`

PoC では partition しない（DuckDB が parquet 1 ファイルでも高速）。

## エラー方針

- `ontology_source` の prefix 不明 → WARNING ログを出して `null` で通す
- `entries[].extract.accession` が input JSONL と join できない → WARNING ログ。samples 行は organism のみ系統 default で埋めて他は null、facts 行は通常通り生成（accession + run_name のみで完結）
- ETL は idempotent。再実行で同じ parquet を上書き

## 配下のテスト

- `tests/fixture/select_minimal.json` … entries 3 件 (ok / extract_failed / mapping_failed の 3 ケース) + errors / run_metadata
- `tests/fixture/bs_entries_chipatlas_minimal.jsonl`, `tests/fixture/bs_entries_rnaseq_minimal.jsonl` … 同 accession の 2 系統 normalize 確認用
- `tests/unit/etl/test_load_input.py` … chip-atlas / rnaseq の normalize が等価な BsInputEntry を返す
- `tests/unit/etl/test_load_select.py` … iter_select_entries が Pydantic validation 通る
- `tests/unit/etl/test_build_facts.py` … extract_status の決定ロジック (truth table 全パス)
- `tests/unit/etl/test_build_samples.py` … source_system / organism_normalized 導出 (ChIP-Atlas 派生は `lib/chip_atlas.py` 側で test)
- `tests/unit/etl/test_build_runs.py` … run_metadata → runs row
- `tests/unit/etl/test_ontology_source.py` … term_id prefix → ontology_source マップ
- `tests/pbt/test_ontology_depth.py` … 任意の DAG で depth = BFS の最小距離 と一致 (hypothesis で hierarchy graph 生成)
- `tests/pbt/test_facts_invariants.py` … 任意の SelectEntry に対し `facts.parquet` row 数 = sum over fields の (extracted 値数 ∪ results 値数, ただし両方 0 なら extract_failed で 1 row)
- `tests/unit/etl/test_load_owl.py` … 小さい OWL fixture から `iter_class_labels` / `iter_subclass_edges` が期待通りの (term_id, label) / (child, parent) を返すこと、blank node / owl:Restriction の subClassOf は無視されること
- `tests/unit/etl/test_build_ontology_restrict.py` … subset_terms と hierarchy_edges を直接渡したとき、subset 外の term が row に含まれないこと、subset 内の transitive closure と depth が正しく入ること
- `tests/unit/etl/test_load_sra_accessions.py` … 小さい TSV fixture を読んで `Type == "EXPERIMENT"` の行が抽出できること、必要 column が dict として yield されること、live 以外の status も保持されること
- `tests/unit/etl/test_build_srx_links.py` … samples.parquet の accession 集合に含まれない BioSample は除外、1 BS : N SRX が複数 row になること、Type が EXPERIMENT 以外の行は出ないこと
