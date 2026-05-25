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

## 画面 2: Cohort drill-down (B 軽量版 + S1 / S2 / S4)

heatmap セルから渡された cohort を一覧化し、外部ツールに持ち出す動線を提供する。

### 入力

- `st.session_state["cohort_filter"]` を読む (heatmap から引き継ぎ)
- 直接 URL でも開けるよう、filter は画面 2 内でも編集可

### 表示

- sample table 列: `accession` / `organism_normalized` / `submission_year` / `project` / `title` / `source_system` / `in_chip_atlas`
- 件数上限: 1 cohort 10000 row 程度を表示上限 (それ以上は集計のみ表示)

### 持ち出し

| ID | 機能 | 対象 | 実装 |
|---|---|---|---|
| S1 | TSV download | 全 sample | `st.download_button` で cohort table を TSV 出力 |
| S2 | DDBJ Search jump | 全 sample | accession ごとに `https://ddbj-search.dbcls.jp/resource/biosample/{accession}` への外部リンク |
| — | ChIP-Atlas top hint | `in_chip_atlas == true` のみ | accession を表示しつつ ChIP-Atlas top (https://chip-atlas.org/) を新規 tab で開くボタン (ユーザーが accession を手で検索)。SRX 単位の deep link は持たない (`docs/data-model.md` chip_atlas_links 節参照) |

S4 (ChIP-Atlas Peak Browser deep link) と S5a (IGV.js 埋め込み) は **PoC scope 外**。SRX 単位の track 連携は `experimentList.tab` parse を伴うため、必要になった時点で `chip_atlas_links.parquet` を別 phase で導入する。

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
- ChIP-Atlas Peak Browser deep link (S4) — SRX 単位連携を必要とするため別 phase
- `chip_atlas_links.parquet` — 上記 S4 / S5a を導入する際に追加 (`docs/data-model.md` 参照)
- Cohort multi-track 比較 (S5b)
- NCBI SRA Run Selector 連携 (S3)
- Galaxy / Workflow 連携 (S6)
