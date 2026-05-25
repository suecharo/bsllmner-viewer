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

- **dimension picker**: x / y それぞれ独立に `(field, ontology_source, depth)` を選ぶ
  - field: `disease` / `cell_line` / `cell_type` / `tissue` / `drug` / `knockout_gene` / `knockdown_gene` / `overexpressed_gene`
  - depth: ontology の roll-up 深さ。`lib/ontology.py:terms_at_depth` で軸 term 集合を取る
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
- ontology term は `lib/ontology.py:terms_at_depth` で軸集合を出し、`facts.term_id` の ancestor を `descendants(axis_term)` で逆引きして集約
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

時系列の sample 分布を俯瞰する補助 view。

### 操作要素

- x / y / size / color picker (各 axis: aggregate metric × group_by)
- `animation_frame = submission_year` (Plotly)
- filter: organism_normalized / source_system / in_chip_atlas

### 表示

- Plotly bubble (`px.scatter` + `animation_frame`)
- セル click 連携は無し (drill-down は画面 1 経由で十分)

### 集計クエリ概要

- `lib/aggregation.py:bubble_pivot(...)` に集約
- 年単位の集計は `samples.submission_year` で group_by

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
