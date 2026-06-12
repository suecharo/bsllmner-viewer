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
| `agg_samples_by_dims.parquet` | 1 row = sample 集計の cell | Home dashboard / sidebar filter の fast path | C+ |
| `agg_field_term_dims.parquet` | 1 row = (field, term_id, dim 組合せ) | Home F5 Pareto / Gapminder の top_terms fast path | C+ |
| `agg_field_status_dims.parquet` | 1 row = facts 集計の cell | Home F3/F4 + Curation D1/D2 の fast path | C+ |

## samples.parquet

| 列 | 型 | Null | 備考 |
|---|---|---|---|
| `accession` | string | not null | PK。`SAMN/SAMD/SAMEA` |
| `organism` | string | nullable | input JSONL の `Description.Organism.OrganismName` から取った **原文**。表記揺れ (例: `Homo Sapiens` / `human` / `Mus Musculus`) はここでは正規化せず保持する。input に対応 entry が無いとき / 値が null・空白のみのときは系統定義の organism (`source_system.organism`) で fallback |
| `organism_normalized` | string | nullable | `organism` を `etl/organism.py:normalize_organism` で正規化した値。`Homo sapiens` / `Mus musculus` / `Mus musculus hybrid` / `xenograft` / `mixed` のいずれか、もしくは未知表記の場合は `organism` と同じ raw 値。input に対応 entry が無いとき / 正規化後が None になったときは `source_system.organism` で fallback |
| `submission_year` | int32 | nullable | input JSONL の `publication_date` (UTC, offset は parse して `.year` を取る) の年。design-memo §12 では submission_date を別ソースから取る代替案あり。PoC では publication_date 採用。input に対応 entry が無いとき null。**`publication_date` が ETL 実行年 (`datetime.now(UTC).year`) より大きいときは null に塗り潰す**: NCBI BioSample の `publication_date` は embargo 解除予定日が入ることがあり (例: 2026 年 ETL 時点で `2027-XX-XX` の sample が存在)、これを信用すると Cohort の `ORDER BY submission_year DESC` で未来年が最上位に並ぶ。embargo 解除待ちの sample は「年が決まっていない」扱いにする |
| `project` | string | nullable | BioProject ID。input の `Attributes.Attribute[?attribute_name=='bioproject_id'].content` |
| `title` | string | nullable | 元 BioSample title (`input.Description.Title`) |
| `source_system` | string | not null | `chip-atlas-hg38` / `chip-atlas-mm10` / `rnaseq-human` / `rnaseq-mouse` (= bsllmner-viewer の系統 ID)。ChIP-Atlas との接続点はこの列で表現する (旧 `in_chip_atlas` を `source_system LIKE 'chip-atlas-%'` に置換、`chip_atlas_genome` は `lib/chip_atlas.py:SOURCE_SYSTEM_TO_GENOME` dict で導出) |
| `run_name` | string | not null | FK → `runs.run_name`。系統 ID と 1:N |
| `sequence_type` | string | nullable | normalize 済み assay name (`ChIP-Seq` / `ATAC-Seq` / `DNase-Seq` / `Bisulfite-Seq` / `RNA-Seq` / `ChIP-Seq (input)` / `Annotation track` / `mixed`)。chip-atlas-* は `experimentList.tab` の `track_type_class` を `etl/seq_type.py:normalize_seq_type` で変換した値を per-SRX に持たせ、BS に紐づく全 SRX を `combine_seq_types` で集約する (2 種以上検出時は `mixed` 固定)。rnaseq-human は `source_system.default_sequence_type` ("RNA-Seq") を使う。決定不能 (SRX 不在 / experimentList.tab cache 不在 / source default なし) は null。UI 側 sidebar / agg parquet では `'(unknown)'` sentinel に塗り潰して 1 軸で扱う (詳細は本ドキュメント末尾 §「sequence_type の null/mixed/(unknown) 取扱」) |
| `srx_first` | string | nullable | accession に紐づく SRX の lexicographically smallest 値 (UI で「first SRX」として表示)。SRX が無い BS では null |
| `srx_count` | int32 | not null | accession に紐づく SRX の総数。SRX が無い BS では 0 |

**生成 base**: samples.parquet は **result file (select_*.json) に出てきた accession** を base に生成する。input JSONL に対応 entry が無い場合は `organism` / `organism_normalized` のみ系統 default で埋め、それ以外の input 由来カラムは null にして WARN ログ。

**ChIP-Atlas 接続点**: かつての `in_chip_atlas` (bool) と `chip_atlas_genome` (string) 列は samples.parquet から削除した。両者は `source_system` の derived (`source_system LIKE 'chip-atlas-%'` / `lib/chip_atlas.py:SOURCE_SYSTEM_TO_GENOME[source_system]`) で 1:1 に決まるため、二重持ちを避けて単一 SoT とする。UI の Cohort 画面 BigWig / Peak BED URL 組立て (`bigwig_url` / `peak_bed_url`) と Home / Gap Discovery の "of which ChIP-Atlas" 派生 metric / overlay は、すべて `lib/chip_atlas.py` の純関数 helper を経由する。

**Cohort 画面のための pre-sort**: `build-samples` の write は `submission_year DESC, accession ASC` 順に sort してから書き出す。Cohort 画面のメイン table は同じ並びの `ORDER BY ... LIMIT 10000` を発行するため、parquet 上で 1.65M 行を再 sort せずに済む。

**SRX 列の埋め方**: `build-samples` 時点では `srx_first=null` / `srx_count=0` で空に初期化する。`build-srx-links` が `srx_links.parquet` を書き出した直後に samples.parquet を読み返し、accession 単位で aggregate (`MIN(srx)` と `COUNT(*)`) して 2 列を in-place で上書きする (`pq.write_table` で tmp → `os.replace` で atomic swap)。Per-SRX 全件展開を inline (`srx_records LIST<STRUCT>`) で持たせる案も検討したが、accession で絞った `srx_links JOIN samples` の方が UNNEST より体感 5x 速かったため、Per-SRX 用は scalar 2 列だけ inline 化し long-form は `srx_links.parquet` を引き続き使う構成にしてある。`build-srx-links` を流していない場合や `SRA_Accessions.tab` cache が無い場合は SRX 列が空のままになり、Cohort 画面では SRX 関連の表示が空になる (warn せず silent fallback)。

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

この一覧は実 data (chip-atlas-hg38 / chip-atlas-mm10 / rnaseq-human / rnaseq-mouse) で出てきた表記を全部カバーする。新しい系統を足す際は本表を更新する。

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

`${BSLLMNER_VIEWER_ONTOLOGY_DIR}/` (default `data/ontology/`) 直下に subset / フル両 OWL を揃える。subset OWL は **term の集合と label を定義する側**、フル OWL は **hierarchy（`rdfs:subClassOf`）を提供する側**。subset OWL は text2term の検索範囲を絞るために CONSTRUCT クエリで切り出したもので、親クラスへの参照は剥がされている（`rdfs:subClassOf` が 0 件）。そのため bsllmner-viewer は subset から term_id / label を取り、hierarchy はフル OWL から補完する。

upstream OWL の download は `scripts/fetch_ontology_owls.py`、subset OWL の生成は `scripts/build_ontology_subsets.sh` (host で実行、`obolibrary/robot:latest` を `docker run` で呼ぶ)。詳細手順は [`docs/etl.md`](etl.md) §「Ontology OWL」を参照。

| ontology_source | subset (term_id + label の source) | hierarchy (`rdfs:subClassOf` の source) | 備考 |
|---|---|---|---|
| `Cellosaurus` | `cellosaurus.owl` | `cellosaurus.owl` | subset を持たないので「全体 = subset」扱い。upstream `cellosaurus.obo` を `build_ontology_subsets.sh` 内で ROBOT convert して生成 (~272 MB)。Cellosaurus の `rdfs:subClassOf` は **すべて `owl:Restriction` 経由の意味的関係** (`derived_from` / `originate_from_same_individual_as` 等) であり、直接の is-a 階層を持たない設計なので、本 ETL では parent edges が 0 件 = 全 term が self-loop のみ (`depth=0`) になる。これは bug ではなく Cellosaurus の仕様 |
| `CL` | `cl_human_subset.owl` + `cl_mouse_subset.owl` を union | `cl.owl` | subset 生成には `efo.owl` も merge する (EFO の細胞 term を CL subset に取り込むため) |
| `UBERON` | `uberon_human_subset.owl` + `uberon_mouse_subset.owl` を union | `uberon.owl` | |
| `MONDO` | `mondo_human_subset.owl` | `mondo.owl` | mouse 用 subset は無く human 用を流用 |
| `ChEBI` | `chebi_subset.owl` | `chebi.owl` | 140MB / 774MB。subset 生成に ROBOT Java heap 24 GB 要 |

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

BioSample → SRA Experiment (SRX) のマッピングを 1 row = 1 SRX で保持する long-form。Cohort 画面のメイン table 表示は samples.parquet の inline scalar 列 (`srx_first` / `srx_count`) で完結するが、**Per-SRX deep-link drill-down はこの parquet を accession で絞って引く** (UNNEST より JOIN の方が速かったため。詳しくは samples.parquet 節を参照)。URL は機械的に組み立て可能なので **parquet には URL を保存せず、ID 列だけ持つ**。UI 側で `f"https://.../{srx}"` 等を組み立てる。

| 列 | 型 | Null | 備考 |
|---|---|---|---|
| `srx` | string | not null | PK。SRA Experiment accession (`SRX*` / `DRX*` / `ERX*`) |
| `accession` | string | not null | FK → `samples.accession` (BioSample)。1 BS : N SRX があり得るので **複合 PK ではなく** SRX 単独 PK |
| `bioproject` | string | nullable | BioProject ID (`PRJNA*` / `PRJDB*` / `PRJEB*` 等) |
| `sra_study` | string | nullable | SRA Study ID (`SRP*` / `DRP*` / `ERP*`) |
| `sra_sample` | string | nullable | SRA Sample ID (`SRS*` / `DRS*` / `ERS*`) |
| `status` | string | not null | NCBI SRA_Accessions の `Status` 列をそのまま (`live` / `suppressed` / `withdrawn` / etc.) |
| `sequence_type` | string | nullable | ChIP-Atlas `experimentList.tab` の column 3 (`track_type_class`) を `normalize_seq_type` で正規化した値。cache 不在 or 未登録 SRX は null |

**生成 base**: NCBI SRA の `SRA_Accessions.tab` を `Type == "EXPERIMENT"` で filter し、さらに `BioSample` 列が `samples.parquet` の `accession` 集合に含まれる行だけを採用する。

**sequence_type 列**: `--experiment-list-tab` (default `data/cache/experimentList.tab`) cache を読んで SRX → `track_type_class` の map を引き、`etl/seq_type.py:normalize_seq_type` で `ChIP-Seq` / `ATAC-Seq` / `DNase-Seq` / `Bisulfite-Seq` / `ChIP-Seq (input)` / `Annotation track` / `RNA-Seq` のいずれかに正規化する。cache 不在 / 未登録 SRX は null のまま。`scripts/fetch_chip_atlas_experiment_list.py` で事前 download する。

**status filter**: ETL では filter せず全 status を保持する。`live` 以外も含めて curation で「suppressed / withdrawn の SRX がどれくらいあるか」を可視化できるようにする。UI 側で必要なら filter する。

**1 BS : N SRX**: 同一 BioSample に複数の SRX が紐づくのは一般的 (replicate / library prep 違い / paired ChIP-Seq の input control 等)。UI は first SRX を cell に表示しつつ「+N more」で残数を示し、その下の Per-SRX deep-link table で `srx_links.parquet` を accession で絞って展開する。

**samples.parquet との関係**: ETL 末尾で `build-srx-links` がこの parquet を書き出した直後、samples.parquet を読み返して accession 単位で `MIN(srx)` と `COUNT(*)` を取り、`srx_first` / `srx_count` の 2 列に inline で焼き込む (`MIN` を実装上は string lexicographic で取るため、SRX 接頭辞が異なるシリーズ間では「最も若い英字 prefix」が選ばれる)。これにより Cohort 画面のメイン table 表示は samples スキャン 1 つで完結する。Per-SRX 全件展開を ARRAY<STRUCT> として inline する案 (`srx_records`) も検討したが、UNNEST より accession 絞り込みの JOIN の方が体感 5x 速かったため採用しなかった。

### URL 組み立て (UI 側)

| ターゲット | URL template |
|---|---|
| NCBI SRA Experiment | `https://www.ncbi.nlm.nih.gov/sra/?term={srx}` |
| DDBJ Search (Experiment) | `https://ddbj-search.dbcls.jp/resource/sra-experiment/{srx}` |
| ChIP-Atlas BigWig | `https://chip-atlas.dbcls.jp/data/{genome}/eachData/bw/{srx}.bw` (`genome` は `lib/chip_atlas.py:SOURCE_SYSTEM_TO_GENOME[source_system]` から導出。`None` の系統 (rnaseq-human 等) では出さない) |
| ChIP-Atlas Peak BED (q < 1E-05) | `https://chip-atlas.dbcls.jp/data/{genome}/eachData/bed05/{srx}.05.bed` |

ChIP-Atlas の BigWig / Peak BED URL は SRX が ChIP-Atlas に登録されているか **保証していない** (404 になり得る)。`source_system LIKE 'chip-atlas-%'` の sample に限って表示する hint であって、確実性は担保しない。

### ChIP-Atlas experimentList.tab との関係

ChIP-Atlas の `experimentList.tab` (https://chip-atlas.dbcls.jp/data/metadata/experimentList.tab) には SRX に対する追加メタデータ (`antigen` / `cell_type_chipatlas` / `genome` 等) が含まれる。これらは PoC スコープでは保持しない (BS 側の bsllmner-mk2 抽出済 facts が同等情報を持つため重複)。必要になったら `srx_links.parquet` に列追加するか、別 parquet で持つ。

## agg_*.parquet (起動高速化 pre-aggregation)

UI cold-start の 13.3M facts.parquet scan を 0 にするため、`build-aggregates` で
samples + facts を一度集計し、~10〜100K 行の小さい parquet として書き出しておく。
`lib/aggregation.py` の `*_fast` helper が読む。agg parquet が無い deployment
では `lib/aggregation.py:has_dashboard_aggregates(con)` が False を返し、
UI 側は元の live 関数 (`samples_by_year_source` 等) に fallback する。

### agg_samples_by_dims.parquet

| 列 | 型 | 備考 |
|---|---|---|
| `submission_year` | int32 | NULL も保持 |
| `source_system` | string | not null。`'chip-atlas-%'` LIKE で ChIP-Atlas 由来かを派生する |
| `sequence_type` | string | NULL は `'(unknown)'` sentinel に塗り潰す (live path も `_filter_clauses` で同じ運用) |
| `organism_normalized` | string | NULL は `'(unknown)'` sentinel に塗り潰す (live path も同上) |
| `sample_count` | int64 | distinct accession 数 |

### agg_field_term_dims.parquet

| 列 | 型 | 備考 |
|---|---|---|
| `field` | string | `VALID_FIELDS` のいずれか |
| `term_id` | string | top 200/field の subset のみ |
| `label` | string | nullable |
| `submission_year` | int32 | |
| `source_system` | string | |
| `sequence_type` | string | NULL は `'(unknown)'` sentinel |
| `organism_normalized` | string | NULL は `'(unknown)'` sentinel |
| `sample_count` | int64 | distinct accession 数 |

field 別に sample_count 上位 200 term だけ残す (`_TOP_TERMS_PER_FIELD`)。
全 term が必要な curation 用途では引き続き live `facts` を読む。

### agg_field_status_dims.parquet

| 列 | 型 | 備考 |
|---|---|---|
| `field` | string | |
| `source_system` | string | |
| `sequence_type` | string | NULL は `'(unknown)'` |
| `submission_year` | int32 | |
| `extract_status` | string | `ok` / `mapping_failed` / `extract_failed` |
| `n` | int64 | fact-row count (BS-distinct ではない) |

Home F3 (per-field status share) / F4 (overall metric trio) / Curation D1 (source × status matrix) / D2 (year × status line) の base。

## sequence_type の null/mixed/(unknown) 取扱

`samples.sequence_type` 列は 3 種類の特殊状態を持つ。ETL / aggregation / UI の各層で扱いが異なるため invariant を本節で SSOT 化する。

| 状態 | 意味 | samples.parquet | agg_*.parquet | UI sidebar | live filter | agg filter |
|---|---|---|---|---|---|---|
| `null` | SRX 不在 / experimentList.tab cache 未登録 / source default なし | `null` のまま | `'(unknown)'` sentinel に塗り潰す (`COALESCE(sequence_type, '(unknown)')`) | option 一覧の末尾に `'(unknown)'` を必ず含める | `IN (...)` リストに `'(unknown)'` が含まれるとき `OR sequence_type IS NULL` を OR で展開 | `IN (..., '(unknown)')` でリテラル一致 |
| `'mixed'` | BS に紐づく SRX が 2 種以上の assay (例: ChIP-Seq + ATAC-Seq) | `'mixed'` (`etl/seq_type.py:combine_seq_types`) | そのまま `'mixed'` | normal な選択肢として表示するが Home donut / Gapminder では 灰色固定 + 末尾配置で de-emphasize | `'mixed' IN (...)` で完全一致 | 同左 |
| 正常値 | `ChIP-Seq` / `ATAC-Seq` / `DNase-Seq` / `Bisulfite-Seq` / `RNA-Seq` / `ChIP-Seq (input)` / `Annotation track` のいずれか | そのまま | そのまま | option 先頭に `_SEQ_TYPE_ORDER` 順で表示 | 完全一致 | 同左 |

**invariant**: 「同じ `SampleFilters` を渡したとき、`*_fast` 関数 (agg path) と live 関数の `sample_count` 合計が一致する」ことを property test (`tests/pbt/lib/test_agg_parity.py`) で保証する。`organism_normalized` も同じ `'(unknown)'` sentinel 運用に統一する。

**mixed の組成追跡**: `samples.sequence_type='mixed'` の BS は組成情報が失われるが、`srx_links.parquet.sequence_type` には per-SRX 値が残っているため、Curation page の D7 (mixed BS の SRX-level 組成 chart) で `srx_links` を accession + sequence_type で集約して可視化する。
