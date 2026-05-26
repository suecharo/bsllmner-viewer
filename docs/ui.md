# UI

bsllmner-viewer の Streamlit UI の **SSOT**。全画面の責務・操作要素・集計クエリ・lib/ 共通層 API を本ドキュメントに集約する。design-memo §7 の検討メモはここに昇格させたもの。仕様変更は本ドキュメントを先に更新してから実装に入る。

## 構成

Streamlit multipage で組む:

```
src/bsllmner_viewer/
  ui/
    Home.py                   -- ナビ + 全体サマリ + ダッシュボード (画面 0)
    pages/
      01_Gap_Discovery.py     -- Gap discovery heatmap (画面 1)
      02_Cohort.py            -- Cohort drill-down (画面 2)
      03_Gapminder.py         -- Gapminder bubble + 派生 view (画面 3)
      04_Curation.py          -- Curation report (画面 4)
  lib/
    duckdb.py                 -- DuckDB connection (cached)
    ontology.py               -- ontology hierarchy helper
    aggregation.py            -- heatmap / bubble / dashboard 共通集計
```

起動: `uv run streamlit run src/bsllmner_viewer/ui/Home.py`

データソースは `${BSLLMNER_VIEWER_DATA_DIR}/parquet/*.parquet` を DuckDB から read-only で読む。in-process / cached connection を 1 個共有する。

filter state (organism_normalized / submission_year range / source_system / in_chip_atlas / ontology_source) は `st.session_state` 経由で 3 画面共通管理する。

## Term info popover (3 画面共通)

term ID を提示する場所には `st.popover` で「term info」を開ける動線を必ず置く。専用ページは作らず、その場で「label / ontology source / depth / 現在 filter 下の sample 件数 / 外部 ontology サイトへのリンク」を一覧化する軽量 UI で完結させる。

### 表示要素

| 要素 | 内容 |
|---|---|
| header | `<label>` `(term_id)` |
| metadata | `source: <ontology_source>` `depth: <int>`（`ontology.parquet` で self-loop 行を引いて取得。未収録 term は `source: <FIELD_TO_ONTOLOGY[field]> (inferred)` で field 側から補完。Cellosaurus / NCBIGene の `inferred` 表示も同じ書き方） |
| metric | 現在 filter 下の `BioSample` 件数 + `of which ChIP-Atlas` |
| link | `Open in <site> ↗`（下表の URL pattern。term_id prefix から導出。未知 prefix なら "No external link for this prefix." と注記） |

### 外部リンク先 (ontology ごとの公式サイト)

| Prefix | site | URL pattern |
|---|---|---|
| `MONDO:` | Monarch Initiative | `https://monarchinitiative.org/disease/MONDO:{local}` |
| `CL:` | EBI OLS (CL) | `https://www.ebi.ac.uk/ols4/ontologies/cl/classes/http%3A%2F%2Fpurl.obolibrary.org%2Fobo%2FCL_{local}` |
| `UBERON:` | EBI OLS (UBERON) | `https://www.ebi.ac.uk/ols4/ontologies/uberon/classes/http%3A%2F%2Fpurl.obolibrary.org%2Fobo%2FUBERON_{local}` |
| `CHEBI:` | EBI ChEBI | `https://www.ebi.ac.uk/chebi/searchId.do?chebiId=CHEBI:{local}` |
| `CVCL:` | Cellosaurus | `https://www.cellosaurus.org/CVCL_{local}` |
| `NCBIGene:` | NCBI Gene | `https://www.ncbi.nlm.nih.gov/gene/{local}` |

OBO Foundry 系 (CL / UBERON) は OLS を term browser として使う（OBO 自体は term ページを持たない）。MONDO は OLS でも見られるが、第一義のソースである Monarch Initiative の disease page に飛ばす。

### 配置 (3 画面)

| 画面 | 設置箇所 |
|---|---|
| Gap Discovery | (a) heatmap の Top N x / y term をそれぞれ並べる "Top axis terms" セクション、(b) 「Send selection to Cohort」テーブルの直下に、選択セル由来の unique (field, term_id) を popover で並べる "Selected cell terms" セクション |
| Cohort | ページ上部の cohort 説明 (`st.info(...)`) の直下に、`facts_terms` / `facts_cells` から抽出した unique (field, term_id) の popover を並べる |
| Gapminder | Top N terms を bubble 下に 1 行ずつ popover で並べる ("Top terms for this field" セクション)。bubble クリック→ popover の遷移は Plotly の API 制約で実装しない |

### 実装方針

- popover 内のクエリは UI 層から `lib/ontology.py:term_summary` + `lib/aggregation.py:term_sample_count` を呼ぶ
- `term_sample_count(field, term_id, filters)` は 1 term ぶんの `COUNT(DISTINCT accession)` と `COUNT(... CASE WHEN in_chip_atlas)` を返す（heatmap pivot の縮約版）
- URL pattern は `lib/ontology.py:external_url(term_id)` に純関数で実装し、tests/unit/lib/test_ontology.py で prefix ごとに pin する
- popover を 1 行で描画する `ui/_term_popover.py:render_term_popover(con, field, term_id, label, filters)` を作り、3 画面から呼ぶ
- popover は `key` を持たないので、ループで複数並べても uniqueness を気にしなくてよい（input widget ではなく container）

## 画面 0: Home dashboard

Landing page。スカラ metric だけでなく、データセット全体の輪郭が一目で掴める dashboard をここに置く。各 chart は sidebar filter を受けない (全 sample 対象の固定 dashboard)。「現在の filter」を意識させない landing 性を優先するため。

### 表示

1. **Top metrics** (現状維持): Runs / BioSamples / of which ChIP-Atlas / Facts / Ontology terms の 5 カード
2. **F1 Yearly submission trend** — `submission_year` × `source_system` の積み上げ area (`px.area`、color = `source_system`)。「いつ・どの系統がどれだけ来たか」
3. **F2 Organism / Source split** — 横並びで `organism_normalized` donut + `source_system` donut の 2 連 (`px.pie(hole=0.5)`)
4. **F3 Field coverage bar** — 各 field の (`ok` / `mapping_failed` / `extract_failed`) を 100% stacked horizontal bar で。「どの field が curation 上問題が多いか」
5. **F4 Mapping success summary** — extract_status の overall ratio を `st.metric` 3 連 (`ok` / `mapping_failed` / `extract_failed`)
6. **F5 Top 10 terms per field** — `disease` / `cell_line` / `tissue` / `drug` を selectbox で切替、`sample_count` 降順の Pareto bar 10 件
7. **Runs table** (現状維持): runs.parquet を直近順に表示

### 集計クエリ概要

下記関数を `lib/aggregation.py` に追加し、Home 側で `@st.cache_data` で wrap する:

- `samples_by_year_source(con) -> DataFrame[submission_year, source_system, sample_count]` (F1)
- `samples_by_organism(con) -> DataFrame[organism_normalized, sample_count]` (F2 左)
- `samples_by_source(con) -> DataFrame[source_system, sample_count]` (F2 右)
- `field_facts_status(con) -> DataFrame[field, extract_status, n]` (F3, F4)
- `top_terms_overall(con, field, top_n) -> DataFrame[term_id, label, sample_count]` (F5)

## 画面 1: Gap discovery (heatmap + sunburst + sankey)

PoC の主軸。「`disease × gene` の sample count を heatmap で可視化し、空白セル = 不足領域を発見」する画面 (design-memo §1)。heatmap だけだと 2 軸クロスしか見えないので、同じ filter / dimension picker を共有しつつ **3 tab 構成** で複数の角度から不足領域を覗ける形にする。

### tab 構成

ページ上部の dimension picker / filter は 3 tab で共有する (filter は sidebar、x/y field と roll-up depth は picker、それ以外の tab 固有 toggle は各 tab 内に置く)。

| tab | 内容 |
|---|---|
| **Heatmap** (default) | 既存の `gap_heatmap_pivot` ベース。後述の display mode selector もここに置く |
| **Sunburst** (B3) | x_field 1 つを `term_hierarchy_breakdown` で root → depth N まで展開した hierarchy を `px.sunburst` で。サイズ = sample_count、色 = ChIP-Atlas ratio (`RdYlGn`)。「ontology subtree のどこに sample が集中 / 不在か」を立体的に見る。root 未指定なら field の全 depth=0 term を併置 (forest)、root を選ぶと subtree drill-in |
| **Sankey** (B4) | x_field と y_field の flow を `field_to_field_flow` → `go.Sankey` で描画。link 太さ = sample_count、ノード color = field。「disease → tissue / disease → drug の流量」が見える。Top N (x), Top N (y) はそれぞれ独立スライダ。BioSample が両 field の facts を持つ場合のみ flow に乗る |

### 操作要素

- **dimension picker**: x / y それぞれ独立に `(field, roll-up depth)` を選ぶ
  - field: `disease` / `cell_line` / `cell_type` / `tissue` / `drug` / `knockout_gene` / `knockdown_gene` / `overexpressed_gene`
  - roll-up depth (default: None = leaf 集計):
    - `None` を選ぶと facts.term_id をそのまま集計 (leaf granular)
    - 0..N を選ぶと「leaf term の祖先のうち depth=N の代表 1 つに丸める」。具体的には ontology.parquet を `(term_id, parent_term_id, depth)` で参照し、`MIN(parent_term_id)` で同一 depth に複数祖先がある場合 (DAG) を決定的に処理する
    - field と ontology_source は `lib/aggregation.py:FIELD_TO_ONTOLOGY` で 1:1 マップ (`disease→MONDO` / `cell_line→Cellosaurus` / `cell_type→CL` / `tissue→UBERON` / `drug→ChEBI`)
    - **Cellosaurus / NCBIGene は roll-up 不可** — Cellosaurus は階層 0 件 (`docs/data-model.md` 参照)、NCBIGene は ontology.parquet に含まない。`cell_line` / `knockout_gene` 等は roll-up picker を出さず leaf 集計のみ
- **filter**:
  - organism_normalized (multi-select)
  - submission_year range (slider)
  - source_system (multi-select)
  - in_chip_atlas (toggle)

### 表示

- Plotly heatmap
- 各セルの値: 該当 cohort の `COUNT(DISTINCT accession)`
- overlay: 「うち N 件 ChIP-Atlas」を hover text に出す (`in_chip_atlas == true` 内訳)
- 空白セル (sample 0) は明示的に色分け (gap が一目で分かるように)

### 表示モード (display mode selector)

heatmap 上部に radio で 4 モード切替を置く。`gap_heatmap_pivot` の戻り値はモード共通で、UI 側で color 軸の値を差し替える:

| モード | color 軸の値 | 用途 |
|---|---|---|
| **BioSample count** (default) | `sample_count` | 既存挙動 |
| **ChIP-Atlas count** | `chip_atlas_count` | ChIP-Atlas に絞った heatmap |
| **ChIP-Atlas ratio** | `chip_atlas_count / sample_count` (sample_count=0 は NaN) | カバー率 (0.0〜1.0) |
| **Gap only (sample > 0, ChIP-Atlas = 0)** | `sample_count` だが、`chip_atlas_count > 0` のセルは NaN にマスク | 「研究はあるが ChIP-Atlas 化されていない」セルだけを赤く強調 |

color scale はモードごとに変える: count 系は `Blues`、ratio は `RdYlGn` (低 ratio = 赤)、gap only は `Reds`。

### 集計クエリ概要

- `facts` × `samples` を `accession` で join
- field を x / y それぞれの選択値で filter
- roll-up depth が指定されたら、CTE で `(term_id → MIN(parent_term_id) WHERE depth = N AND ontology_source = ?)` の置換 map を作り、`facts.term_id` を rolled term に LEFT JOIN で置換。`COALESCE(rolled, term_id)` で「depth=N の祖先がなければ leaf 自身」を fallback
- 軸 label は ontology.parquet から `(rolled_term, label WHERE parent_term_id = term_id)` で再取得 (facts.label は leaf のラベルなので使えない)
- pivot は `lib/aggregation.py:gap_heatmap_pivot(...)` に集約

### B3 Sunburst 集計クエリ概要

- `term_hierarchy_breakdown(con, field, filters, root_term=None, max_depth, roll_up_depth=None)` を呼ぶ
- ontology.parquet から `field` の primary ontology (`FIELD_TO_ONTOLOGY[field]`) の `(term_id, parent_term_id, depth)` を読み、root_term を起点に depth ≤ max_depth の subtree を抽出
- 各 term について `facts × samples` を当てて `sample_count` / `chip_atlas_count` を計算
- `px.sunburst` が要求する long-form `(term_id, parent_term_id, label, sample_count, chip_atlas_count, depth)` を返す。root term の `parent_term_id` は空文字 (Plotly の sunburst root 表現)
- Cellosaurus / NCBIGene は階層が無いので tab 内で「This field has no usable hierarchy」と表示して chart は出さない

### B4 Sankey 集計クエリ概要

- `field_to_field_flow(con, x_field, y_field, filters, top_n_x, top_n_y, x_roll_up_depth, y_roll_up_depth)` を呼ぶ
- 既存 `gap_heatmap_pivot` の戻りそのものが long-form `(x_term_id, x_label, y_term_id, y_label, sample_count, chip_atlas_count)` で sankey に必要な形と同じ。新規関数は内部で `gap_heatmap_pivot` を呼んで sample_count > 0 の cell だけに絞り、`x_label` / `y_label` の prefix で重複しないように `{x_field}: {label}` / `{y_field}: {label}` の node 名を組み立てて返す形にする (Plotly Sankey は node 名 unique 要求)
- UI 側で node list / source / target / value の 3 つに射影して `go.Sankey` に渡す

### 遷移

- セル click → 画面 2 (Cohort drill-down) に accession set + filter state を渡す
- `st.session_state["cohort_filter"]` に query 条件を入れ、page_link で遷移

## 画面 2: Cohort drill-down (B 軽量版 + S1 / S2 / S3 / S4 / S5)

heatmap セルから渡された cohort を一覧化し、外部ツールに持ち出す動線を提供する。

### 入力

- `st.session_state["cohort_filter"]` を読む (heatmap から引き継ぎ)
- 直接 URL でも開けるよう、filter は画面 2 内でも編集可

### 表示

- sample table 列 (左から):
  - `accession` (BioSample)
  - `organism_normalized` / `submission_year` / `title` / `source_system` / `in_chip_atlas`
  - `srx`: 該当 BioSample に紐づく first SRX。複数あるとき `SRX1234567 (+N more)` 表記
  - 任意の ontology 列: `cell_line` / `cell_type` / `tissue` / `disease` / `drug` / `knockout_gene` / `knockdown_gene` / `overexpressed_gene` のうちユーザが選択した field を per-BioSample で `label (term_id)` の `, ` 結合で表示 (term 毎に dedupe、label 昇順)
- ontology 列の選択は table 直上の expander 「Ontology columns」内 multiselect で行い、default は VALID_FIELDS 全 8 field を ON。値は filter に依存せず `accession` 単位で facts 全 run を union するので、同 BioSample が複数 run で評価されていても全 term が見える
- 取得は `cohort_facts_columns(con, accessions, fields)` (long-form `(accession, field, value)` を返す) を呼んで UI 側で pivot → `df.merge(on='accession')`。selected_fields が空なら追加 query を打たない
- srx は samples.parquet が inline で持つ scalar 列 (`srx_first` / `srx_count`) を `cohort_samples` がそのまま返すので、メイン table は追加クエリなしに `srx` (= `srx_first`) と `srx_count` を表示し、`+N more` 表記を組み立てる。SRX を持たない BioSample は `srx_count=0`
- 件数上限: 1 cohort 10000 row 程度を表示上限 (それ以上は集計のみ表示)
- `project` / `chip_atlas_genome` は main sample table には出さない (前者は内容が空 or BioProject の bp4 行で実用情報が薄い、後者は ChIP-Atlas deep link 構築用の内部 carry-over で UI に出す意味が無いため)。ChIP-Atlas BigWig / Peak BED は下記 Per-SRX 展開 table 側で `cohort_srx_links` が samples から carry-over した `chip_atlas_genome` を用いて組み立てる

### Cohort 構成 mini chart (C2)

cohort sample table の直上に、cohort の輪郭を 3 つの compact bar chart で示す。`cohort_breakdown(con, filters, facts_terms, facts_cells)` で `(submission_year, organism_normalized, source_system, sample_count)` を 1 query で取得し、UI で 3 軸それぞれに pivot:

| 列 | chart | 軸 |
|---|---|---|
| 左 | Submission year histogram | x = year, y = sample_count, color 単一 |
| 中 | Organism bar | x = organism_normalized, y = sample_count |
| 右 | Source system bar | x = source_system, y = sample_count |

各 chart は `height=180` 程度に抑えた `px.bar` で、`st.columns(3)` に並べる。cohort 全体集計なので table の 10K cap には影響されない (集計は SQL 側、cap は表示のみ)。

### 持ち出し

| ID | 機能 | 対象 | 実装 |
|---|---|---|---|
| S1 | TSV download | 全 sample | `st.download_button` で cohort table を TSV 出力 (SRX deep link 列込み) |
| S2 | DDBJ Search jump (BioSample) | 全 sample | accession ごとに `https://ddbj-search.dbcls.jp/resource/biosample/{accession}` |
| S3 | NCBI SRA jump (SRX) | 全 SRX | `https://www.ncbi.nlm.nih.gov/sra/?term={srx}` |
| S3' | DDBJ Search jump (SRX) | 同上 | `https://ddbj-search.dbcls.jp/resource/sra-experiment/{srx}` |
| S4 | ChIP-Atlas BigWig | `in_chip_atlas == true` の sample に紐づく全 SRX | `https://chip-atlas.dbcls.jp/data/{chip_atlas_genome}/eachData/bw/{srx}.bw` |
| S5 | ChIP-Atlas Peak BED (q < 1E-05) | 同上 | `https://chip-atlas.dbcls.jp/data/{chip_atlas_genome}/eachData/bed05/{srx}.05.bed` |

ChIP-Atlas BigWig / Peak BED は SRX が ChIP-Atlas に登録されているか保証しない (404 になり得る)。`in_chip_atlas` flag は系統由来で「ChIP-Atlas 系統の sample である」しか担保しないため、UI は「hint」として提示する。

### Per-SRX 展開 table

main の sample table は 1 row = 1 BioSample のままで `srx (first)` 列に first SRX のみ出す (`+N more` 表記でカーディナリティを伝える)。その下に「Per-SRX deep links」section を別途置き、**1 row = 1 SRX に展開した table** で全 SRX の deep link を click 可能にする。これで `+N more` の中身も UI から到達できる。

- データソース: `lib/aggregation.py:cohort_srx_links(con, accessions, limit=500)`
  - メイン table から取った BioSample accession list を渡し、`srx_links JOIN samples` を accession で絞り込んで long-form を返す (`chip_atlas_genome` は samples 側からの carry-over)
  - 列: `accession` (BioSample) / `srx` / `bioproject` / `sra_study` / `sra_sample` / `status` / `chip_atlas_genome`
  - 並び順: `accession, srx`
  - `limit` は SRX 単位 (default 500)。上限超過は warning で明示
  - 設計上、samples.parquet の inline 列に `srx_records LIST<STRUCT>` を持って UNNEST する案も検討したが、accession で絞った `srx_links JOIN samples` の方が体感 5x 速かったため (1.65M sample の全行 ARRAY deserialize を避けられる)、Per-SRX 用には `srx_links.parquet` を引き続き利用する
- UI: BioSample 列を上位に表示しつつ各 SRX 行に NCBI / DDBJ / ChIP-Atlas BigWig / Peak BED を LinkColumn で出す。同 BioSample が複数 SRX を持つ場合、accession 列は値が同じ行が並ぶ
- データの起点: `srx_links.parquet` は `build-srx-links` が生成する。`build-srx-links` を流していない / `SRA_Accessions.tab` cache が無い場合は空表示になり「No SRX records for this cohort」を caption で出す

S5a (IGV.js 埋め込み) は **PoC scope 外**。

### Pinned cohort 比較 (C4)

「現在 cohort」と「session に pin した cohort」の overlap を見る軽量比較機構。Cohort 画面の operator が `disease=X` の cohort を見た後、`disease=Y` の cohort に切り替えた時に「両者で共通する BioSample がどれだけあるか」を即座に確認するための機能。

#### Pin / Clear 操作

main の sample table の直上、3 連 mini chart の下に expander `🔖 Pinned cohort` を置く:

- Pin されていないとき: `Pin this cohort` ボタン。押すと現在の `(filters, facts_terms, facts_cells)` を `st.session_state["pinned_cohort"]` に shallow copy で保存し、当時の `accession` リスト (上限 50K) も `pinned_cohort_accessions` に焼き込む (filter / facts を後から見直しても比較対象は固定)。pin した瞬間の cohort label (例: `disease=neoplasm × organism=Homo sapiens`) を一緒に保存し、後で表示
- Pin されているとき: pinned cohort の label + サイズを caption で示し、`Compare with pinned` toggle (default off) と `Clear pin` ボタンを並べる。toggle on で比較セクションを下に展開

#### 比較セクション

`Compare with pinned` toggle on のときだけ描画する。`cohort_overlap_summary(con, accessions_a, accessions_b) -> dict` で `only_a` / `only_b` / `both` の 3 値を集計:

| 表示要素 | 内容 |
|---|---|
| `st.metric` 3 連 | `Pinned only` / `Both` / `Current only` の sample 数 |
| Venn (2-set) | Plotly `shapes` で半透明の 2 円 (左 = pinned、右 = current)、中央に both、外側にそれぞれ only。色は左右で別、overlap は混色 |
| Per-field 重なり棒グラフ | 各 field (disease / cell_type / ...) について、両 cohort に出現する term の Jaccard 係数を horizontal bar で。「同じ disease 構成か / drug 構成は違うか」が一目 |

per-field 比較は `cohort_term_overlap(con, accessions_a, accessions_b, fields=None) -> DataFrame[field, n_pinned, n_current, n_both, jaccard]` を別関数で出す。`cohort_overlap_summary` は accession ベースの 1 query で済むが、per-field は facts を join するので別 query にする。

`pinned_cohort_accessions` は session を跨いだら消えるが、PoC では複数 user 状態を持たない (`docs/ui.md` 最下部 scope 外参照) ので問題ない。

## 画面 3: Gapminder bubble (C)

時系列の sample 分布を俯瞰する補助 view。デフォルトは **累積集計 × log Y 軸 × log bubble size** で「bubble が時間とともに成長する Gapminder 風の軌跡」を見せる。3 つの toggle で挙動を切り替えられる。

### tab 構成

`st.tabs` で 10 view 並列。前半 5 tab (Bubble / Trajectory / Rank race / Heatmap / Composition) は組成と軌跡の俯瞰、後半 5 tab (Slope / Momentum / Diversity / Concentration / Treemap) は成長率・多様性・階層を補完する:

- **Bubble** (default): `px.scatter` + `animation_frame=submission_year`。Y 軸 = `count`、X 軸 = term の numeric index (tickvals/ticktext で label 復元、animation frame またぎで category が消えない)、size = bubble_size、color = organism_normalized。各 (term, organism) を `animation_group` で繋ぎ frame 推移で軌跡を見せる
- **Trajectory**: animation 抜きの line chart。X 軸 = year、Y 軸 = count、色 = term。一目で「どの term が伸びたか」が見える
- **Rank race** (A1): `px.bar(orientation="h")` + `animation_frame=submission_year`。各 frame で top N term を `count` 降順に horizontal bar として描く。`category_orders` を frame ごとに固定せず Plotly の auto sort に任せると bar の入れ替わりがアニメーションになる。bar 上に rank 値 (`1.`, `2.`, ...) を text label で表示
- **Heatmap** (A4): static `px.imshow`、Y 軸 = top N term (overall rank 降順)、X 軸 = year、color = log(count)。アニメ抜きで全期間を 1 枚に。dense なほど色が濃い
- **Composition** (A2/A3): `px.area` で構成変化。toggle で `Stacked area` (絶対量) / `100% stacked` (`groupnorm="percent"`) を切替。X 軸 = year、色 = term、面積 = count。Streamgraph (中央 baseline) は Plotly native 非対応なので簡略版として stacked area で代替
- **Slope** (A5): 2 年スナップショット slope graph。2 つの year を pickup (default: data の min 年と max 年) し、各 term の 2 点を line で結ぶ。傾きが急 = 急成長 / 急減衰。色 = term、Y 軸 = count (log toggle 適用)、X 軸 = 2 値の年 category。`bubble_dataset` の戻りを 2 年だけ pick して描画 (新規集計関数なしで実装)
- **Momentum** (A6): YoY momentum scatter。`momentum_dataset` の戻り (`term × year × count_abs × count_delta × count_cum`) から最新年だけを scatter。X = `count_abs` (現在絶対量、log toggle)、Y = `count_delta` (前年差)、size = `count_cum` (累積、log toggle)、color = term。右上 = 「絶対量も多く成長もしてる」ホットスポット
- **Diversity** (A8): cumulative diversity curve。`cumulative_diversity` 関数で年単位の累積 unique term 数を取り、line chart で描画。toggle で全体 / organism 別 / source 別を切替。Y 軸 log toggle で「ontology 空間の充足が頭打ちか」を見る
- **Concentration** (A9): Gini + Shannon entropy over time。`concentration_over_time` の戻り (`year × gini × shannon`) を 2 系列 line chart で。Y 軸 = 値 (0..1)、X 軸 = year。「研究テーマは集中 / 分散か」「年ごとの不均等度」を見る。Gini と Shannon は別 Y 軸ではなく値域が共に [0,1] (Shannon は theoretical max で正規化) で同じ axis に重ねる
- **Treemap** (A11): ontology hierarchy treemap、year picker で snapshot を切替 (Plotly Express の `treemap` は `animation_frame` 非対応のため、static + year selectbox で代替)。`term_hierarchy_breakdown(field, filters, max_depth=2..3, by_year=True)` で全 year 分まとめて取り、UI 側で 1 年分に絞って `px.treemap(ids=term_id, parents=parent_term_id, values=sample_count, color=chip_atlas_ratio)` で描画。`field` が roll-up 非対応 (Cellosaurus / NCBIGene) の場合は tab 内で "no hierarchy" caption

### 操作要素

- field picker (`disease` / `cell_line` / ...)
- Top N terms slider
- **Roll-up depth (ontology)** selectbox — Gap Discovery と同じ depth picker。`FIELD_TO_ONTOLOGY` に該当する field (`disease` / `cell_type` / `tissue` / `drug`) のみ表示し、`leaf` (default) / 0..5 を選択。non-roll-up field は caption で「disabled」と説明
- **Aggregation** radio (Cumulative / Per-year)
  - **Cumulative** (default): `cumulative_bubble_dataset(...)` の戻り値で `(term, organism)` ごとに year reindex + cumsum。`count` 列 = `sample_count_cum`
  - **Per-year**: `bubble_dataset(...)` の戻り値そのまま。`count` 列 = `sample_count`。data の無い年は frame が空になる (Cumulative より「動き」は少ないが、年あたり新規 sample の分布が見える)
- **Log scale Y axis** toggle (default on)
  - on: `fig.update_yaxes(type="log")` + range_y = `[0.8, max*1.4]`
  - off: `type="linear"` + range_y = `[0, max*1.1]`
  - bubble / line 両方に同時適用
- **Log scale bubble size** toggle (default on)
  - on: `bubble_size = log10(count.clip(1)) + 1` で正規化 (16K vs 100 が画面上で同程度の比率で描画される)
  - off: `bubble_size = count` の linear (最大値が画面を覆い、小さい値が消える Gapminder 原典挙動)

### 再正規化の原則

toggle / filter / field のどれかが変わったら、表示パラメータ (axis range、size scaling、X 軸 category order) を新条件から再計算する。具体的には:

- `@st.cache_data` の hash key に `mode` / `field` / `top_n` / filter を全部含めて、入力変化で raw data が新規取得される
- raw data 取得後の `df.copy() → bubble_size 計算 → category_order 算出 → x_pos マッピング` は **cache の外** で毎回実行する。`y_log` / `size_log` を flip しただけでも再正規化が走る
- `st.plotly_chart(fig, key=...)` の key に全 toggle/filter を結合した文字列を入れ、Plotly DOM の使い回しで axes range が古いまま残らないようにする

### 集計クエリ概要

- `lib/aggregation.py:bubble_dataset(field, filters, top_n, roll_up_depth=None)` で `(submission_year, term_id, label, organism_normalized, sample_count, chip_atlas_count)` の年単位 long-form data を取得。`roll_up_depth` 指定時は heatmap と同じく `_axis_facts_sql` の rolled CTE を経由し、leaf を depth=N の祖先に置換した上で集計
- `cumulative_bubble_dataset(field, filters, top_n, roll_up_depth=None)` は `bubble_dataset` を呼び `(term_id, organism_normalized)` ごとに year で reindex + 0 fill + cumsum。累積 column は `sample_count_cum` / `chip_atlas_count_cum`
- UI 側で mode に応じてどちらを呼ぶか分岐し、`count` / `chip_count` という共通 column 名に rename して下流処理を 1 本化
- Rank race / Heatmap / Composition / Slope tab は organism を集約する必要があるため、`bubble_dataset` / `cumulative_bubble_dataset` の戻りを pandas で `(submission_year, term_id, label)` で再集計してから描画する (新 aggregation 関数は追加しない)
- **Momentum tab (A6)** は `lib/aggregation.py:momentum_dataset(field, filters, top_n, roll_up_depth=None)` を呼ぶ。内部で `bubble_dataset` を 1 回呼んで `(term_id)` ごとに year reindex + cumsum + diff を取り、`(term_id, label, year, count_abs, count_delta, count_cum)` を返す。UI 側は最新 year だけ pick して scatter
- **Diversity tab (A8)** は `cumulative_diversity(field, filters, group_by, roll_up_depth=None)` を呼ぶ。`group_by` は `None` / `"organism_normalized"` / `"source_system"` の 3 値。SQL で `(year, group_value, COUNT(DISTINCT term_id))` を取り、pandas で `(group_value)` ごとに year reindex + cumulative DISTINCT (= 既出 term の集合を逐次積算) を計算する。SQL の `COUNT(DISTINCT ...)` だけでは「その年までに見た unique term 数」が出ないので、Python で `expanding apply` する
- **Concentration tab (A9)** は `concentration_over_time(field, filters, roll_up_depth=None)` を呼ぶ。SQL で `(year, term_id, sample_count)` を取り、pandas で年ごとに gini / shannon (max-normalized) を計算して `(year, n_terms, total_samples, gini, shannon)` を返す
- **Treemap tab (A11)** は `term_hierarchy_breakdown(field, filters, root_term=None, max_depth, roll_up_depth=None, by_year=True)` を呼ぶ。Sunburst (画面 1 B3) と同じ集計関数を年次対応にした版。`by_year=True` のとき各 year × 各 term の sample_count を返す (`(year, term_id, parent_term_id, label, depth, sample_count, chip_atlas_count)`)、`False` のとき year を抜いた全期間集計を返す

## 画面 4: Curation report

bsllmner-mk2 へのフィードバックループ用ページ。LLM 抽出 + ontology mapping の品質を curation 観点で可視化する。design-memo §9 E1〜E6 のうち PoC では D1〜D5 を Streamlit 内で実装し、それ以外は Tier 3+ で追加。

### 表示

1. **D1 Mapping status heatmap (field × source_system)** — 行 = field、列 = source_system、値 = 各 (field, source_system) における `extract_status` 別の fact 比率。色付けは `ok` 率 (高ければ緑、低ければ赤、`RdYlGn`)。hover で `ok` / `mapping_failed` / `extract_failed` の 3 値内訳を表示
2. **D2 Mapping success over time** — field selectbox + line chart。X = submission_year、Y = `ok` 比率、color = field (multi-line)。「prompt / ontology 改善が時系列で効いているか」
3. **D3 Top N unmapped raw values** — field selectbox + Top N slider + horizontal bar。`extract_status = mapping_failed` の facts で `value` 別 row 数 (`n`) と `sample_count` を bar 2 系列で。「次に ontology mapping を加えるべき語彙候補」
4. **D4 Run timeline (Gantt)** — `runs.parquet` の `start_time` / `end_time` を `px.timeline` で帯状描画。`y = run_name`、色 = `status` (`completed` / `failed` / `interrupted` / `running` を別色)、hover で `model` / `total_entries` / `error_count` / `processing_time_sec`。`end_time` が null の場合は `start_time + processing_time_sec` (秒) で代用、それでも算出不能なら `start_time + 1 分` (running 状態の bar 表示用)。filter は **適用しない** (run はそもそも sample より集合が小さく、cohort filter で run を絞る意義が薄い)
5. **D5 Raw value → term mapping (Sankey)** — `extract_status = ok` の facts で `(value, term_id)` の集約を `go.Sankey` で。field selectbox + Top N slider + min count slider。左 nodes = raw value、右 nodes = term (label)、link 太さ = fact 数 (= sample 数の代理)。「同じ raw value が複数 term にマップされてないか」「逆に同じ term に多数の表記揺れが流れているか」が一目で発見できる。link は `n >= min_count` で間引き

### 集計クエリ概要

- `mapping_status_matrix(con, filters) -> DataFrame[field, source_system, extract_status, n]` (D1)
- `mapping_status_over_time(con, filters) -> DataFrame[field, submission_year, extract_status, n]` (D2)
- `top_unmapped_values(con, field, top_n, filters) -> DataFrame[value, n, sample_count]` (D3、`term_id IS NULL AND value IS NOT NULL`)
- D4 は集計関数を追加しない。`get_conn().execute("SELECT ... FROM runs ORDER BY start_time")` を UI 側で 1 回呼ぶ。runs.parquet は数十行程度なので cache 不要
- `raw_value_term_flow(con, field, top_n, filters, min_count=1) -> DataFrame[value, term_id, label, n, sample_count]` (D5、`extract_status = 'ok' AND term_id IS NOT NULL AND value IS NOT NULL`)

sidebar filter は D1/D2/D3/D5 に適用する。D4 は run 単位のメタなので filter 非適用。`@st.cache_data` で UI 側 cache。

## lib/ 共通層 API

### lib/duckdb.py

| 関数 | 用途 |
|---|---|
| `get_conn() -> duckdb.DuckDBPyConnection` | `@st.cache_resource` で 1 個共有。`${BSLLMNER_VIEWER_DATA_DIR}/parquet/*.parquet` を `CREATE VIEW` で曝す |

### lib/ontology.py

`ontology.parquet` を DuckDB 経由で query する hierarchy helper。

| 関数 | 用途 |
|---|---|
| `descendants(term_id: str, source: str) -> list[str]` | subtree query。`WHERE parent_term_id = ? AND ontology_source = ?` の単純 query (transitive 展開 + self-loop 済み)。Cellosaurus は parent edges 0 件で全 self-loop のため、`descendants("CVCL:...")` は基本「自分自身のみ」を返す (data-model.md 参照) |
| `ancestors(term_id: str, source: str) -> list[str]` | 逆向き。`WHERE term_id = ?` |
| `label(term_id: str) -> str \| None` | term_id → label 引き |
| `roots(source: str) -> list[str]` | `WHERE depth = 0` |
| `terms_at_depth(source: str, depth: int) -> list[str]` | heatmap 軸 roll-up 用 |
| `term_summary(term_id: str) -> TermSummary` | popover 用。`(term_id, label, ontology_source, depth)` を 1 query で返す。`ontology.parquet` に term が無い場合は `label/source/depth = None` |
| `external_url(term_id: str) -> str \| None` | term_id prefix から ontology 公式サイトの URL を組み立てる純関数。未知 prefix は `None` |

実装方針:

- `lib/duckdb.py:get_conn()` を使う純関数 (Streamlit 依存を持ち込まない)
- query 結果の cache は UI 呼び出し側で `@st.cache_data` する (lib/ 内では cache を持たない)
- unit test を tests/unit/lib/ 配下に置く

### lib/aggregation.py

heatmap / bubble / dashboard / curation の共通集計関数。

| 関数 | 用途 |
|---|---|
| `gap_heatmap_pivot(x_axis, y_axis, filters, ...) -> pd.DataFrame` | 画面 1 (Gap Discovery) |
| `bubble_dataset(field, filters, top_n) -> pd.DataFrame` | 画面 3 (Bubble / Trajectory per-year) |
| `cumulative_bubble_dataset(field, filters, top_n) -> pd.DataFrame` | 画面 3 (Cumulative) |
| `top_terms(field, filters, top_n, roll_up_depth) -> list[tuple[str,str]]` | heatmap/bubble の Top N 選定 |
| `cohort_samples(filters, facts_terms, facts_cells, limit) -> pd.DataFrame` | 画面 2 sample table |
| `cohort_facts_columns(con, accessions, fields) -> pd.DataFrame[accession,field,value]` | 画面 2 ontology 列追加 (long-form、UI で pivot) |
| `cohort_count(filters, facts_terms, facts_cells) -> int` | 画面 2 件数 |
| `cohort_breakdown(filters, facts_terms, facts_cells) -> pd.DataFrame[year,organism,source,count]` | 画面 2 mini chart (C2) |
| `term_sample_count(field, term_id, filters) -> tuple[int,int]` | term popover |
| `samples_by_year_source(con) -> pd.DataFrame` | Home F1 |
| `samples_by_organism(con) -> pd.DataFrame` | Home F2 |
| `samples_by_source(con) -> pd.DataFrame` | Home F2 |
| `field_facts_status(con) -> pd.DataFrame[field,extract_status,n]` | Home F3 / F4 |
| `top_terms_overall(con, field, top_n) -> pd.DataFrame[term_id,label,sample_count]` | Home F5 |
| `mapping_status_matrix(con, filters) -> pd.DataFrame[field,source_system,extract_status,n]` | Curation D1 |
| `mapping_status_over_time(con, filters) -> pd.DataFrame[field,submission_year,extract_status,n]` | Curation D2 |
| `top_unmapped_values(con, field, top_n, filters) -> pd.DataFrame[value,n,sample_count]` | Curation D3 |
| `momentum_dataset(con, field, filters, top_n, roll_up_depth) -> pd.DataFrame[term_id,label,submission_year,count_abs,count_delta,count_cum]` | Gapminder A6 |
| `cumulative_diversity(con, field, filters, group_by, roll_up_depth) -> pd.DataFrame[submission_year,group_value,unique_terms,cum_unique_terms]` | Gapminder A8 |
| `concentration_over_time(con, field, filters, roll_up_depth) -> pd.DataFrame[submission_year,n_terms,total_samples,gini,shannon]` | Gapminder A9 |
| `term_hierarchy_breakdown(con, field, filters, root_term, max_depth, roll_up_depth, by_year) -> pd.DataFrame[submission_year?,term_id,parent_term_id,label,depth,sample_count,chip_atlas_count]` | Gap Discovery B3 / Gapminder A11 |
| `field_to_field_flow(con, x_field, y_field, filters, top_n_x, top_n_y, x_roll_up_depth, y_roll_up_depth) -> pd.DataFrame[x_term_id,x_label,y_term_id,y_label,sample_count,chip_atlas_count]` | Gap Discovery B4 |
| `cohort_overlap_summary(con, accessions_a, accessions_b) -> dict[only_a,both,only_b]` | Cohort C4 metric |
| `cohort_term_overlap(con, accessions_a, accessions_b, fields=None) -> pd.DataFrame[field,n_pinned,n_current,n_both,jaccard]` | Cohort C4 per-field |
| `raw_value_term_flow(con, field, top_n, filters, min_count) -> pd.DataFrame[value,term_id,label,n,sample_count]` | Curation D5 |

## 開発ステップ (PoC スコープ)

1. **C-1**: lib/duckdb.py + lib/ontology.py + ontology helper の unit test
2. **C-2**: 画面 1 (Gap discovery heatmap) + lib/aggregation.py:gap_heatmap_pivot
3. **C-3**: 画面 2 (Cohort drill-down) + S1 / S2 (S4 は Phase C 完了後)
4. **C-4**: 画面 3 (Gapminder bubble) + lib/aggregation.py:bubble_pivot

各 step で `uv run pytest` / `ruff` / `mypy` clean + 実 data smoke。

## scope 外 (PoC では作らない)

- 認証 (ローカル demo 前提)
- 複数 user 状態 (`st.session_state` のみで完結)
- E2E (Streamlit ブラウザテスト)
- IGV.js 埋め込み (S5a) — design-memo §10 Step 6 として別 phase で追加
- Cohort multi-track 比較 (S5b)
- NCBI SRA Run Selector 連携 (Run Selector 全機能再現)
- Galaxy / Workflow 連携 (S6)
- 複数 SRX を持つ BioSample の expander 展開 UI (現状は first SRX だけ cell に出す)
