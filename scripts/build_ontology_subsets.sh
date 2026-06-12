#!/usr/bin/env bash
# bsllmner-viewer の build_ontology が読む OWL を生成する:
#   - cellosaurus.owl              (cellosaurus.obo → ROBOT convert)
#   - cl_{human,mouse}_subset.owl  (cl.owl + efo.owl)
#   - uberon_{human,mouse}_subset.owl
#   - mondo_human_subset.owl
#   - chebi_subset.owl
#
# 内部で obolibrary/robot:latest を `docker run` で起動するため、Docker-in-Docker
# 回避の都合で **host から** 実行する想定。subset 生成は idempotent (既存ファイル
# は --force 無しなら skip)。
#
# 元 script: bsllmner-mk2 `scripts/build_subset_ontologies.sh`
# (PO / NCBI Gene / cellosaurus_{human,mouse} 系統は viewer では使わないので削除、
#  cellosaurus.obo → cellosaurus.owl の convert を追加)

set -euo pipefail

FORCE=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    -h|--help)
      cat <<'EOF'
Usage: scripts/build_ontology_subsets.sh [--force]

bsllmner-viewer の build_ontology が読む OWL を ${BSLLMNER_VIEWER_ONTOLOGY_DIR}
(default: data/ontology) に生成する。

Prerequisites:
  - Upstream OWL/OBO が ${BSLLMNER_VIEWER_ONTOLOGY_DIR} 配下に揃っていること:
    cellosaurus.obo, cl.owl, efo.owl, uberon.owl, mondo.owl, chebi.owl
    (uv run python scripts/fetch_ontology_owls.py で取得)
  - host から docker が叩け、obolibrary/robot:latest が pull できること
  - git (sh-ikeda/ontology-constructor-for-bsllmner を work/ に clone する)

Options:
  --force    既存の生成物があっても再生成する
EOF
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/.." && pwd)"

# DATA_DIR / ONTOLOGY_DIR は env 優先、未指定なら repo の data/ontology を使う。
if [[ -n "${BSLLMNER_VIEWER_ONTOLOGY_DIR:-}" ]]; then
  ONTOLOGY_DIR="${BSLLMNER_VIEWER_ONTOLOGY_DIR}"
else
  DATA_DIR="${BSLLMNER_VIEWER_DATA_DIR:-${REPO_ROOT}/data}"
  ONTOLOGY_DIR="${DATA_DIR}/ontology"
fi
WORK_DIR="${REPO_ROOT}/work"
SH_IKEDA_DIR="${WORK_DIR}/ontology-constructor-for-bsllmner"

mkdir -p "${ONTOLOGY_DIR}"

missing=()
for f in cellosaurus.obo cl.owl efo.owl uberon.owl mondo.owl chebi.owl; do
  [[ -f "${ONTOLOGY_DIR}/${f}" ]] || missing+=("${f}")
done
if (( ${#missing[@]} > 0 )); then
  echo "Missing upstream OWL/OBO under ${ONTOLOGY_DIR}: ${missing[*]}" >&2
  echo "Run 'uv run python scripts/fetch_ontology_owls.py' first." >&2
  exit 3
fi

mkdir -p "${WORK_DIR}"
if [[ ! -d "${SH_IKEDA_DIR}/.git" ]]; then
  echo "Cloning sh-ikeda/ontology-constructor-for-bsllmner into ${SH_IKEDA_DIR}"
  git clone --depth 1 https://github.com/sh-ikeda/ontology-constructor-for-bsllmner "${SH_IKEDA_DIR}"
else
  echo "Updating ${SH_IKEDA_DIR}"
  git -C "${SH_IKEDA_DIR}" pull --ff-only
fi

robot() {
  local heap="$1"; shift
  docker run --rm \
    -e ROBOT_JAVA_ARGS="${heap}" \
    -v "${ONTOLOGY_DIR}:/work" \
    -v "${SH_IKEDA_DIR}:/queries" \
    -v "${HERE}:/scripts" \
    -w /work \
    obolibrary/robot:latest \
    robot "$@"
}

declare_owl_class() {
  # scripts/declare_owl_class.rq を適用し rdf:type owl:Class を補う。
  local infile="$1"
  local outfile="$2"
  robot "-Xmx8g" query --input "${infile}" --update /scripts/declare_owl_class.rq --output "${outfile}"
}

skip_if_exists() {
  local out="$1"
  if (( FORCE == 0 )) && [[ -f "${ONTOLOGY_DIR}/${out}" ]]; then
    echo "Skip (exists): ${out}"
    return 1
  fi
  return 0
}

cleanup() {
  for f in "$@"; do
    rm -f "${ONTOLOGY_DIR}/${f}"
  done
}

build_cellosaurus_owl() {
  # cellosaurus.obo → cellosaurus.owl の ROBOT convert。
  # viewer の build_ontology は `cellosaurus.owl` を 1 ファイルそのまま読むので
  # taxid filter (mk2 の preprocess_cellosaurus.py) は適用しない。
  robot "-Xmx8g" convert --input cellosaurus.obo --output cellosaurus.owl --format owl
}

build_cl_variant() {
  # $1: variant ("human" or "mouse"), $2: output subset owl name
  local variant="$1"
  local out="$2"
  local cl_ttl="_cl_${variant}.ttl"
  local merged_ttl="_cl_${variant}_with_efo.ttl"
  local merged_owl="_cl_${variant}_with_efo.owl"

  robot "-Xmx8g" query --input cl.owl --query "/queries/cl/cl_construct_${variant}.rq" "${cl_ttl}"
  if [[ ! -f "${ONTOLOGY_DIR}/_efo_cell.ttl" ]]; then
    robot "-Xmx8g" query --input efo.owl --query /queries/cl/efo_construct.rq _efo_cell.ttl
  fi
  cat "${ONTOLOGY_DIR}/${cl_ttl}" "${ONTOLOGY_DIR}/_efo_cell.ttl" > "${ONTOLOGY_DIR}/${merged_ttl}"
  robot "-Xmx8g" convert --input "${merged_ttl}" --format owl --output "${merged_owl}"
  declare_owl_class "${merged_owl}" "${out}"
  cleanup "${cl_ttl}" "${merged_ttl}" "${merged_owl}"
}

build_simple_subset() {
  # $1: source OWL (e.g. uberon.owl), $2: query path, $3: output subset owl name
  local src="$1"
  local query="$2"
  local out="$3"
  local stem="_${out%.owl}"
  local tmp_ttl="${stem}.ttl"
  local tmp_owl="${stem}_pre.owl"

  robot "-Xmx8g" query --input "${src}" --query "${query}" "${tmp_ttl}"
  robot "-Xmx8g" convert --input "${tmp_ttl}" --format owl --output "${tmp_owl}"
  declare_owl_class "${tmp_owl}" "${out}"
  cleanup "${tmp_ttl}" "${tmp_owl}"
}

build_chebi_subset() {
  robot "-Xmx24g" query --input chebi.owl --update /queries/chebi/chebi_update.rq --output _chebi_role.owl
  robot "-Xmx24g" query --input _chebi_role.owl --query /queries/chebi/chebi_construct.rq _chebi_mod.ttl
  robot "-Xmx8g" convert --input _chebi_mod.ttl --format owl --output _chebi_pre.owl
  declare_owl_class _chebi_pre.owl chebi_subset.owl
  cleanup _chebi_role.owl _chebi_mod.ttl _chebi_pre.owl
}

if skip_if_exists cellosaurus.owl; then
  build_cellosaurus_owl
fi

if skip_if_exists cl_human_subset.owl; then
  build_cl_variant human cl_human_subset.owl
fi

if skip_if_exists cl_mouse_subset.owl; then
  build_cl_variant mouse cl_mouse_subset.owl
fi
cleanup _efo_cell.ttl

if skip_if_exists uberon_human_subset.owl; then
  build_simple_subset uberon.owl /queries/uberon/uberon_construct_human.rq uberon_human_subset.owl
fi

if skip_if_exists uberon_mouse_subset.owl; then
  build_simple_subset uberon.owl /queries/uberon/uberon_construct_mouse.rq uberon_mouse_subset.owl
fi

if skip_if_exists mondo_human_subset.owl; then
  build_simple_subset mondo.owl /queries/mondo/mondo_construct_human.rq mondo_human_subset.owl
fi

if skip_if_exists chebi_subset.owl; then
  build_chebi_subset
fi

echo ""
echo "Done. Generated OWLs under ${ONTOLOGY_DIR}:"
ls -lh "${ONTOLOGY_DIR}"/cellosaurus.owl "${ONTOLOGY_DIR}"/*_subset.owl 2>/dev/null || true
