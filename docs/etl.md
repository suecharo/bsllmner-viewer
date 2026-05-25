# ETL

bsllmner-mk2 の出力 + OWL → bsllmner-viewer の parquet ファイル群を生成する pipeline の仕様。

スキーマの SSOT は [data-model.md](data-model.md)。本ドキュメントは「どこから何を読んで、どう加工するか」に絞る。

## 入力 source

### 1. bsllmner-mk2 出力 (`${BSLLMNER_VIEWER_DATA_DIR}/`)

3 系統。系統 = (source_system, organism, in_chip_atlas, chip_atlas_genome) の組:

| source_system | organism | in_chip_atlas | chip_atlas_genome | input | result |
|---|---|---|---|---|---|
| `chip-atlas-hg38` | Homo sapiens | true | hg38 | `data/chip-atlas-hg38/input/bs_entries_hg38.jsonl` (1 file) | `data/chip-atlas-hg38/result/select_*.json` (1 file, 1.2GB) |
| `chip-atlas-mm10` | Mus musculus | true | mm10 | `data/chip-atlas-mm10/input/bs_entries_mm10.jsonl` (1 file) | `data/chip-atlas-mm10/result/select_*.json` (1 file, 1.0GB) |
| `rnaseq-human` | Homo sapiens | false | null | `data/rnaseq-human/input/bs_entries_YYYY-MM-01_YYYY-MM-DD.jsonl` (60 files) | `data/rnaseq-human/result/select_rnaseq_*_YYYY-MM.json` (60 files) |

系統定義は `src/bsllmner_viewer/etl/sources.py` に table 駆動で書く（path 解決と (source_system, file) → entries iterator は系統定義から逆引きできる）。

### 2. bsllmner-mk2 OWL (`${BSLLMNER_VIEWER_BSLLMNER_MK2_ONTOLOGY_DIR}/`)

read-only bind mount。data-model.md「対象 ontology と source ファイル」参照。

bsllmner-viewer はこれらを **読むだけ**。生成 (`build_subset_ontologies.sh` 等) は bsllmner-mk2 側の責務。

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
| `build-samples` | `samples.parquet` | input JSONL + select_*.json の accession 集合を join。in_chip_atlas / chip_atlas_genome は系統定義から導出 |
| `build-facts` | `facts.parquet` | select_*.json の entries を long format 展開 (extract_status 含む) |
| `build-ontology` | `ontology.parquet` | 対象 OWL を順次 parse + transitive closure |
| `build-all` | 全 parquet | 上記を依存順 (runs → samples → facts → ontology) で実行 |

各 subcommand は `--source-system` で系統を絞れる (省略時は全系統)。`--out-dir` で出力先を override 可能 (default `${BSLLMNER_VIEWER_DATA_DIR}/parquet`)。

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
- `tests/unit/etl/test_build_samples.py` … in_chip_atlas / chip_atlas_genome 導出
- `tests/unit/etl/test_build_runs.py` … run_metadata → runs row
- `tests/unit/etl/test_ontology_source.py` … term_id prefix → ontology_source マップ
- `tests/pbt/test_ontology_depth.py` … 任意の DAG で depth = BFS の最小距離 と一致 (hypothesis で hierarchy graph 生成)
- `tests/pbt/test_facts_invariants.py` … 任意の SelectEntry に対し `facts.parquet` row 数 = sum over fields の (extracted 値数 ∪ results 値数, ただし両方 0 なら extract_failed で 1 row)

OWL parse の test は OWL fixture 用意が重いので integration test として後回し（実 OWL で動作確認するだけ）。
