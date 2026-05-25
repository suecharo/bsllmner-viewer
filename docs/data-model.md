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
| `srx_links.parquet` | 1 row = 1 SRA Experiment | BioSample → SRX deep link 生成用 | C+ |

## samples.parquet

| 列 | 型 | Null | 備考 |
|---|---|---|---|
| `accession` | string | not null | PK。`SAMN/SAMD/SAMEA` |
| `organism` | string | nullable | input JSONL の `Description.Organism.OrganismName` から取った **原文**。表記揺れ (例: `Homo Sapiens` / `human` / `Mus Musculus`) はここでは正規化せず保持する。input に対応 entry が無いとき / 値が null・空白のみのときは系統定義の organism (`source_system.organism`) で fallback |
| `organism_normalized` | string | nullable | `organism` を `etl/organism.py:normalize_organism` で正規化した値。`Homo sapiens` / `Mus musculus` / `Mus musculus hybrid` / `xenograft` / `mixed` のいずれか、もしくは未知表記の場合は `organism` と同じ raw 値。input に対応 entry が無いとき / 正規化後が None になったときは `source_system.organism` で fallback |
| `submission_year` | int32 | nullable | input JSONL の `publication_date` (UTC, offset は parse して `.year` を取る) の年。design-memo §12 では submission_date を別ソースから取る代替案あり。PoC では publication_date 採用。input に対応 entry が無いとき null |
| `project` | string | nullable | BioProject ID。input の `Attributes.Attribute[?attribute_name=='bioproject_id'].content` |
| `title` | string | nullable | 元 BioSample title (`input.Description.Title`) |
| `source_system` | string | not null | `chip-atlas-hg38` / `chip-atlas-mm10` / `rnaseq-human` (= bsllmner-viewer の系統 ID) |
| `run_name` | string | not null | FK → `runs.run_name`。系統 ID と 1:N |
| `in_chip_atlas` | bool | not null | `source_system in ('chip-atlas-hg38','chip-atlas-mm10')` で導出。本 PoC では SRX 単位連携を作らないため、この flag が ChIP-Atlas との接続点 |
| `chip_atlas_genome` | string | nullable | `hg38` / `mm10` / null。`source_system` から導出 |

**生成 base**: samples.parquet は **result file (select_*.json) に出てきた accession** を base に生成する。input JSONL に対応 entry が無い場合は `organism` / `organism_normalized` のみ系統 default で埋め、それ以外の input 由来カラムは null にして WARN ログ。

**重複 accession の扱い**: 同じ accession が複数 run に出る場合（再実行など）、`run_name` 単位で 1 row 持つ。primary view は最新 `run_name` 優先で UI 側で解決。

### organism_normalized の正規化ルール

`etl/organism.py:normalize_organism(raw)` で実装する純関数。挙動は以下:

1. `raw` が None / 空白のみ → `None` を返す (呼び出し側で系統 default に fallback)
2. 文字列に `xenograft` を含む (case-insensitive) → `"xenograft"`
3. 文字列に `mixed` を含む (case-insensitive) → `"mixed"`
4. ` x ` を含み、かつ `mus` を含む (case-insensitive) → `"Mus musculus hybrid"` (例: `Mus musculus x Mus spretus`, `Mus musculus musculus x Mus musculus castaneus`)
5. 以下の case-insensitive 完全一致表は固定値に正規化:

    | 入力 (lower) | 出力 |
    |---|---|
    | `homo sapiens`, `homo sapien`, `human` | `Homo sapiens` |
    | `mus musculus`, `mouse` | `Mus musculus` |
    | `mus musculus domesticus`, `mus musculus musculus`, `mus musculus castaneus` | `Mus musculus` |

6. 上記いずれにも該当しない場合は **raw 文字列 (strip 済み) をそのまま返す**。UI / Curation で「想定外 organism」として可視化できるよう、未知の表記は隠さない

この一覧は実 data (chip-atlas-hg38 / chip-atlas-mm10 / rnaseq-human) で出てきた表記を全部カバーする。新しい系統を足す際は本表を更新する。

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

bsllmner-mk2 の `ontology/` を read-only bind mount で参照する。subset OWL は **term の集合と label を定義する側**、フル OWL は **hierarchy（`rdfs:subClassOf`）を提供する側**。subset OWL は bsllmner-mk2 が text2term の検索範囲を絞るために生成したもので、親クラスへの参照は剥がされている（`rdfs:subClassOf` が 0 件）。そのため bsllmner-viewer は subset から term_id / label を取り、hierarchy はフル OWL から補完する。

| ontology_source | subset (term_id + label の source) | hierarchy (`rdfs:subClassOf` の source) | 備考 |
|---|---|---|---|
| `Cellosaurus` | `cellosaurus.owl` | `cellosaurus.owl` | subset を持たないので「全体 = subset」扱い。272MB。Cellosaurus の `rdfs:subClassOf` は **すべて `owl:Restriction` 経由の意味的関係** (`derived_from` / `originate_from_same_individual_as` 等) であり、直接の is-a 階層を持たない設計なので、本 ETL では parent edges が 0 件 = 全 term が self-loop のみ (`depth=0`) になる。これは bug ではなく Cellosaurus の仕様 |
| `CL` | `cl_human_subset.owl` + `cl_mouse_subset.owl` を union | `cl.owl` | |
| `UBERON` | `uberon_human_subset.owl` + `uberon_mouse_subset.owl` を union | `uberon.owl` | |
| `MONDO` | `mondo_human_subset.owl` | `mondo.owl` | mouse 用 subset は無く human 用を流用（bsllmner-mk2 ontology.md 記載通り） |
| `ChEBI` | `chebi_subset.owl` | `chebi.owl` | 140MB / 774MB |

NCBI Gene は hierarchy が薄いため `ontology.parquet` に含めない（design-memo §6）。

### parse / hierarchy 抽出

- ライブラリ: `lxml.etree.iterparse`（streaming）。owlready2 / rdflib は使わない（フル OWL が 700MB クラスのため in-memory load を避ける）
- subset OWL を streaming parse して `(term_id, label)` を集める（= subset の term 集合 + label map）
- フル OWL を streaming parse して `(child_term_id, parent_term_id)` のペアを取り、**child と parent の両方が subset の term 集合に含まれる pair だけ採用**する。subset 外の term には親が伸びないので、subset 外の祖先 term は `ontology.parquet` に出現しない
- 採用した parent map に対して transitive closure を取り、`(term_id, parent_term_id)` を全 ancestor 分 row 化（自分自身も `parent_term_id = term_id` で 1 row 入れる）
- `depth` は subset 内グラフ上で BFS。root（subset 内に親を持たない term）を depth=0、複数 root なら最小値
- `rdfs:subClassOf` の object が blank node や `owl:Restriction` の場合は無視（`rdf:resource` 属性付きで直接 IRI を参照しているものだけ採用する）

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

## srx_links.parquet

BioSample → SRA Experiment (SRX) のマッピング。Cohort 画面で各 sample に NCBI / DDBJ Search / ChIP-Atlas BigWig / Peak BED の deep link を出すのに使う。URL は機械的に組み立て可能なので **parquet には URL を保存せず、ID 列だけ持つ**。UI 側で `f"https://.../{srx}"` 等を組み立てる。

| 列 | 型 | Null | 備考 |
|---|---|---|---|
| `srx` | string | not null | PK。SRA Experiment accession (`SRX*` / `DRX*` / `ERX*`) |
| `accession` | string | not null | FK → `samples.accession` (BioSample)。1 BS : N SRX があり得るので **複合 PK ではなく** SRX 単独 PK |
| `bioproject` | string | nullable | BioProject ID (`PRJNA*` / `PRJDB*` / `PRJEB*` 等) |
| `sra_study` | string | nullable | SRA Study ID (`SRP*` / `DRP*` / `ERP*`) |
| `sra_sample` | string | nullable | SRA Sample ID (`SRS*` / `DRS*` / `ERS*`) |
| `status` | string | not null | NCBI SRA_Accessions の `Status` 列をそのまま (`live` / `suppressed` / `withdrawn` / etc.) |

**生成 base**: NCBI SRA の `SRA_Accessions.tab` を `Type == "EXPERIMENT"` で filter し、さらに `BioSample` 列が `samples.parquet` の `accession` 集合に含まれる行だけを採用する。

**status filter**: ETL では filter せず全 status を保持する。`live` 以外も含めて curation で「suppressed / withdrawn の SRX がどれくらいあるか」を可視化できるようにする。UI 側で必要なら filter する。

**1 BS : N SRX**: 同一 BioSample に複数の SRX が紐づくのは一般的 (replicate / library prep 違い / paired ChIP-Seq の input control 等)。UI は first SRX を cell に表示しつつ「+N more」で残数を示し、expander 等で展開できるようにする。

### URL 組み立て (UI 側)

| ターゲット | URL template |
|---|---|
| NCBI SRA Experiment | `https://www.ncbi.nlm.nih.gov/sra/?term={srx}` |
| DDBJ Search (Experiment) | `https://ddbj-search.dbcls.jp/resource/sra-experiment/{srx}` |
| ChIP-Atlas BigWig | `https://chip-atlas.dbcls.jp/data/{chip_atlas_genome}/eachData/bw/{srx}.bw` (`chip_atlas_genome` は samples 側から引く。`null` の sample では出さない) |
| ChIP-Atlas Peak BED (q < 1E-05) | `https://chip-atlas.dbcls.jp/data/{chip_atlas_genome}/eachData/bed05/{srx}.05.bed` |

ChIP-Atlas の BigWig / Peak BED URL は SRX が ChIP-Atlas に登録されているか **保証していない** (404 になり得る)。`samples.in_chip_atlas == true` の sample に限って表示する hint であって、確実性は担保しない。

### ChIP-Atlas experimentList.tab との関係

ChIP-Atlas の `experimentList.tab` (https://chip-atlas.dbcls.jp/data/metadata/experimentList.tab) には SRX に対する追加メタデータ (`antigen` / `cell_type_chipatlas` / `genome` 等) が含まれる。これらは PoC スコープでは保持しない (BS 側の bsllmner-mk2 抽出済 facts が同等情報を持つため重複)。必要になったら `srx_links.parquet` に列追加するか、別 parquet で持つ。
