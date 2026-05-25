# Data Model

bsllmner-viewer が DuckDB から参照する parquet の **SSOT**。スキーマ変更が必要な場合は本ドキュメントを先に更新し、合意してから ETL / UI を変更する。

物理的には `${BSLLMNER_VIEWER_DATA_DIR}/parquet/*.parquet` に置く。

## ファイル一覧

| ファイル | 粒度 | 用途 | Phase |
|---|---|---|---|
| `samples.parquet` | 1 row = 1 BioSample | primary table | A |
| `facts.parquet` | 1 row = (BioSample × field × value) | long-format LLM 抽出結果 | A |
| `runs.parquet` | 1 row = 1 bsllmner-mk2 run | run metadata / curation report 用 | A |
| `ontology.parquet` | 1 row = (term × parent) | term hierarchy (transitive 展開済) | A |
| `chip_atlas_links.parquet` | 1 row = 1 SRX | BioSample → ChIP-Atlas track 連携 | C |

## samples.parquet

| 列 | 型 | Null | 備考 |
|---|---|---|---|
| `accession` | string | not null | PK。`SAMN/SAMD/SAMEA` |
| `organism` | string | nullable | `Homo sapiens` / `Mus musculus`。input JSONL の `Description.Organism.OrganismName` から。input に対応 entry が無いときは系統定義の organism (`source_system.organism`) で fallback |
| `submission_year` | int32 | nullable | input JSONL の `publication_date` (UTC, offset は parse して `.year` を取る) の年。design-memo §12 では submission_date を別ソースから取る代替案あり。PoC では publication_date 採用。input に対応 entry が無いとき null |
| `project` | string | nullable | BioProject ID。input の `Attributes.Attribute[?attribute_name=='bioproject_id'].content` |
| `title` | string | nullable | 元 BioSample title (`input.Description.Title`) |
| `source_system` | string | not null | `chip-atlas-hg38` / `chip-atlas-mm10` / `rnaseq-human` (= bsllmner-viewer の系統 ID) |
| `run_name` | string | not null | FK → `runs.run_name`。系統 ID と 1:N |
| `in_chip_atlas` | bool | not null | Phase A では `source_system in ('chip-atlas-hg38','chip-atlas-mm10')` で導出。Phase D で chip_atlas_links と join した正確な flag に更新 |
| `chip_atlas_genome` | string | nullable | `hg38` / `mm10` / null。Phase A では `source_system` から導出 |
| `chip_atlas_srx_count` | int32 | not null (default 0) | Phase C / D で更新。Phase A は常に 0 |

**生成 base**: samples.parquet は **result file (select_*.json) に出てきた accession** を base に生成する。input JSONL に対応 entry が無い場合は `organism` のみ系統 default で埋め、それ以外の input 由来カラムは null にして WARN ログ。

**重複 accession の扱い**: 同じ accession が複数 run に出る場合（再実行など）、`run_name` 単位で 1 row 持つ。primary view は最新 `run_name` 優先で UI 側で解決。

## facts.parquet

| 列 | 型 | Null | 備考 |
|---|---|---|---|
| `accession` | string | not null | FK → `samples.accession` |
| `run_name` | string | not null | FK → `runs.run_name` |
| `field` | string | not null | `cell_line` / `cell_type` / `tissue` / `disease` / `drug` / `knockout_gene` / `knockdown_gene` / `overexpressed_gene` |
| `value` | string | nullable | LLM 抽出生値。`entries[].extract.extracted[field]` 由来。extraction 失敗時は `null` |
| `term_id` | string | nullable | mapping 後 ontology term ID (`CVCL:0030` 等)。`entries[].results[field][i].term_id`。mapping 失敗時は `null` |
| `label` | string | nullable | term preferred label |
| `exact_match` | bool | nullable | Stage 2a exact hit か |
| `text2term_score` | float32 | nullable | Stage 2b 採用時のスコア。`ResolvedValue.reasoning` の `"text2term score: ([0-9.]+)"` から正規表現で抽出 |
| `ontology_source` | string | nullable | `term_id` の prefix から導出。下記マップ参照 |
| `extract_status` | string | not null | `ok` / `extract_failed` / `mapping_failed` のいずれか。E1 / E2 / E4 curation report で使う |

### ontology_source の導出規則 (term_id prefix → source)

| Prefix | ontology_source |
|---|---|
| `CVCL:` | `Cellosaurus` |
| `CL:` | `CL` |
| `UBERON:` | `UBERON` |
| `MONDO:` | `MONDO` |
| `CHEBI:` | `ChEBI` |
| `NCBIGene:` | `NCBIGene` (PoC では使うが ontology.parquet には含めない) |

該当しない prefix が出たら ETL 側で WARNING を出して `ontology_source = null` のまま通す。

### extract_status の決定ロジック

| `extract.extracted[field]` | `results[field]` | extract_status |
|---|---|---|
| `null` | `[]` | `extract_failed`（LLM が抽出しなかった） |
| not null | `[]` | `mapping_failed`（LLM 抽出はあったが ontology mapping 失敗） |
| not null | non-empty | `ok` |

extraction 失敗 / mapping 失敗の row も必ず保持する（curation report E1, E2, E4 のため）。

### array 型 field の展開

`drug` / `knockout_gene` / `knockdown_gene` / `overexpressed_gene` は `value_type: array`。`results[field]` は複数 `ResolvedValue` を持ち得るので、それぞれ 1 row として展開する（同じ `(accession, run_name, field)` で複数 row）。

## runs.parquet

| 列 | 型 | Null | 備考 |
|---|---|---|---|
| `run_name` | string | not null | PK。SelectResult.run_metadata.run_name |
| `source_system` | string | not null | 系統 ID |
| `model` | string | not null | LLM モデル名 (`mistral-small3.1:24b` 等) |
| `start_time` | timestamp[us, UTC] | not null | |
| `end_time` | timestamp[us, UTC] | nullable | |
| `status` | string | not null | `completed` / `failed` / `interrupted` / `running` |
| `total_entries` | int32 | not null | |
| `error_count` | int32 | not null (default 0) | `len(SelectResult.errors)` |
| `processing_time_sec` | float64 | nullable | `run_metadata.processing_time_sec` |

## ontology.parquet

| 列 | 型 | Null | 備考 |
|---|---|---|---|
| `term_id` | string | not null | 例: `CVCL:0030`, `MONDO:0005061` |
| `ontology_source` | string | not null | `Cellosaurus` / `CL` / `UBERON` / `MONDO` / `ChEBI` |
| `label` | string | nullable | `rdfs:label` or `skos:prefLabel` |
| `parent_term_id` | string | not null | 1 子 = 複数行で transitive 展開。**自分自身も `parent_term_id = term_id` で 1 行入れる**（self loop で「自身を含む subtree query」を簡単化） |
| `depth` | int32 | not null | root からの最短距離。複数 root の場合は min(distance)。root 自身は `depth = 0` |

### 対象 ontology と source ファイル

bsllmner-mk2 の `ontology/` を read-only bind mount で参照する。

| ontology_source | source file (bsllmner-mk2/ontology/) | 備考 |
|---|---|---|
| `Cellosaurus` | `cellosaurus.owl` | full 版（subset の `cellosaurus_human/mouse.owl` は bsllmner-mk2 Docker で生成、bsllmner-viewer 側では生成しない）。272MB |
| `CL` | `cl_human_subset.owl` + `cl_mouse_subset.owl` を union | |
| `UBERON` | `uberon_human_subset.owl` + `uberon_mouse_subset.owl` を union | |
| `MONDO` | `mondo_human_subset.owl` | mouse 用 subset は無く human 用を流用（bsllmner-mk2 ontology.md 記載通り） |
| `ChEBI` | `chebi_subset.owl` | 140MB |

NCBI Gene は hierarchy が薄いため `ontology.parquet` に含めない（design-memo §6）。

### parse / hierarchy 抽出

- ライブラリ: `owlready2`
- `rdfs:subClassOf` を辿って transitive closure を取る
- 複数親の場合、すべての (term, parent) ペアを row 化（重複 row は許容、`(term_id, parent_term_id)` で unique）
- `depth` は BFS で求める。root を depth=0 とし、複数 root の場合は最小値

### 命名統一: URI → term_id

OWL 内の term URI を以下で正規化する:

| URI prefix | term_id prefix |
|---|---|
| `http://purl.obolibrary.org/obo/CL_` | `CL:` |
| `http://purl.obolibrary.org/obo/UBERON_` | `UBERON:` |
| `http://purl.obolibrary.org/obo/MONDO_` | `MONDO:` |
| `http://purl.obolibrary.org/obo/CHEBI_` | `CHEBI:` |
| `http://purl.obolibrary.org/obo/Cellosaurus#CVCL_` | `CVCL:` |

bsllmner-mk2 の SelectResult が返す `term_id` も同じ形式なので join 可能。

## chip_atlas_links.parquet (Phase C)

design-memo §6 のまま。Phase A では生成しない。

| 列 | 型 | 備考 |
|---|---|---|
| `srx` | string | PK |
| `accession` | string | FK → samples.accession |
| `genome` | string | `hg38` / `mm10` |
| `bigwig_url` | string | |
| `peak_url` | string | |
| `browser_url` | string | |
| `antigen` | string | |
| `cell_type_chipatlas` | string | |

Source: ChIP-Atlas `experimentList.tab` + NCBI `SRA_Accessions.tab`（design-memo §12 Open Question）。
