# UI

bsllmner-viewer の Streamlit UI の **SSOT**。3 画面の責務・操作要素・集計クエリ・lib/ 共通層 API を本ドキュメントに集約する。design-memo §7 の検討メモはここに昇格させたもの。仕様変更は本ドキュメントを先に更新してから実装に入る。

## 構成

Streamlit multipage で組む:

```
src/bsllmner_viewer/
  ui/
    Home.py                   -- ナビ + 全体サマリ (sample 数 / runs / chip-atlas overlay)
    pages/
      01_gap_discovery.py     -- Gap discovery heatmap (画面 1)
      02_cohort.py            -- Cohort drill-down (画面 2)
      03_gapminder.py         -- Gapminder bubble (画面 3)
  lib/
    duckdb.py                 -- DuckDB connection (cached)
    ontology.py               -- ontology hierarchy helper
    aggregation.py            -- heatmap / bubble 共通の pivot 集計
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

## 画面 1: Gap discovery heatmap (A + G overlay)

PoC の主軸。「`disease × gene` の sample count を heatmap で可視化し、空白セル = 不足領域を発見」する画面 (design-memo §1)。

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

### 集計クエリ概要

- `facts` × `samples` を `accession` で join
- field を x / y それぞれの選択値で filter
- roll-up depth が指定されたら、CTE で `(term_id → MIN(parent_term_id) WHERE depth = N AND ontology_source = ?)` の置換 map を作り、`facts.term_id` を rolled term に LEFT JOIN で置換。`COALESCE(rolled, term_id)` で「depth=N の祖先がなければ leaf 自身」を fallback
- 軸 label は ontology.parquet から `(rolled_term, label WHERE parent_term_id = term_id)` で再取得 (facts.label は leaf のラベルなので使えない)
- pivot は `lib/aggregation.py:gap_heatmap_pivot(...)` に集約

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
  - `organism_normalized` / `submission_year` / `project` / `title` / `source_system` / `in_chip_atlas`
  - `srx`: 該当 BioSample に紐づく first SRX。複数あるとき `SRX1234567 (+N more)` 表記
  - `ncbi_sra` / `ddbj_sra` / `chip_atlas_bw` / `chip_atlas_bed`: first SRX の deep link (LinkColumn)。後 2 つは `in_chip_atlas == true` の sample のみ非空文字
- srx は `lib/aggregation.py:cohort_samples` 内で `srx_links` を `accession` で LEFT JOIN、`MIN(srx)` を first として採用、`COUNT(srx) - 1` を `srx_more_count` として保持する
- 件数上限: 1 cohort 10000 row 程度を表示上限 (それ以上は集計のみ表示)

### 持ち出し

| ID | 機能 | 対象 | 実装 |
|---|---|---|---|
| S1 | TSV download | 全 sample | `st.download_button` で cohort table を TSV 出力 (SRX deep link 列込み) |
| S2 | DDBJ Search jump (BioSample) | 全 sample | accession ごとに `https://ddbj-search.dbcls.jp/resource/biosample/{accession}` |
| S3 | NCBI SRA jump (SRX) | first SRX を持つ sample | `https://www.ncbi.nlm.nih.gov/sra/?term={srx}` |
| S3' | DDBJ Search jump (SRX) | 同上 | `https://ddbj-search.dbcls.jp/resource/sra-experiment/{srx}` |
| S4 | ChIP-Atlas BigWig | `in_chip_atlas == true` かつ first SRX を持つ sample | `https://chip-atlas.dbcls.jp/data/{chip_atlas_genome}/eachData/bw/{srx}.bw` |
| S5 | ChIP-Atlas Peak BED (q < 1E-05) | 同上 | `https://chip-atlas.dbcls.jp/data/{chip_atlas_genome}/eachData/bed05/{srx}.05.bed` |

ChIP-Atlas BigWig / Peak BED は SRX が ChIP-Atlas に登録されているか保証しない (404 になり得る)。`in_chip_atlas` flag は系統由来で「ChIP-Atlas 系統の sample である」しか担保しないため、UI は「hint」として提示する。

複数 SRX を持つ BioSample は table cell に first だけ出すが、TSV download は **first しか出さない** (PoC スコープ。第二弾以降に expander で全 SRX を見せる UI を検討)。S5a (IGV.js 埋め込み) は **PoC scope 外**。

## 画面 3: Gapminder bubble (C)

時系列の sample 分布を俯瞰する補助 view。デフォルトは **累積集計 × log Y 軸 × log bubble size** で「bubble が時間とともに成長する Gapminder 風の軌跡」を見せる。3 つの toggle で挙動を切り替えられる。

### tab 構成

`st.tabs` で 2 view 並列:

- **Bubble** (default): `px.scatter` + `animation_frame=submission_year`。Y 軸 = `count`、X 軸 = term の numeric index (tickvals/ticktext で label 復元、animation frame またぎで category が消えない)、size = bubble_size、color = organism_normalized。各 (term, organism) を `animation_group` で繋ぎ frame 推移で軌跡を見せる
- **Trajectory**: animation 抜きの line chart。X 軸 = year、Y 軸 = count、色 = term。一目で「どの term が伸びたか」が見える
- (将来) rank race / ChIP-Atlas overlay 等を tab で追加していく

### 操作要素

- field picker (`disease` / `cell_line` / ...)
- Top N terms slider
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

- `lib/aggregation.py:bubble_dataset(...)` で `(submission_year, term_id, label, organism_normalized, sample_count, chip_atlas_count)` の年単位 long-form data を取得
- `cumulative_bubble_dataset(...)` は `bubble_dataset` を呼び `(term_id, organism_normalized)` ごとに year で reindex + 0 fill + cumsum。累積 column は `sample_count_cum` / `chip_atlas_count_cum`
- UI 側で mode に応じてどちらを呼ぶか分岐し、`count` / `chip_count` という共通 column 名に rename して下流処理を 1 本化

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

heatmap / bubble の共通 pivot 集計を関数化する。実装は heatmap 着手時に確定 (現段階では責務分割だけ宣言)。

| 関数 | 用途 |
|---|---|
| `gap_heatmap_pivot(x_axis, y_axis, filters) -> pd.DataFrame` | 画面 1 用 |
| `bubble_pivot(x, y, size, color, filters) -> pd.DataFrame` | 画面 3 用 |
| `term_sample_count(field, term_id, filters) -> tuple[int, int]` | popover 用。1 (field, term_id) の `(sample_count, chip_atlas_count)` を返す |

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
