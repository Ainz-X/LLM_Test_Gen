#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/env.sh"

OUTPUT_CSV="${1:-$A3_ROOT/LLM_Test_Gen/Data/Method_Context.csv}"
JAR_PATH="$A3_ROOT/LLM_Test_Gen/Java_Scripts/method-context-extractor/target/method-context-extractor-0.2.0-SNAPSHOT-jar-with-dependencies.jar"

if [[ ! -f "$JAR_PATH" ]]; then
  echo "Extractor JAR not found: $JAR_PATH" >&2
  exit 1
fi

rm -f "$OUTPUT_CSV"

extract_project() {
  local workspace_dir="$1"
  local class_fqn="$2"
  local append_flag="${3:-}"
  local project_dir="$A3_ROOT/$workspace_dir"

  local src_dir
  local bin_dir
  src_dir=$("$A3_DEFECTS4J" export -p dir.src.classes -w "$project_dir" | tail -n 1)
  bin_dir=$("$A3_DEFECTS4J" export -p dir.bin.classes -w "$project_dir" | tail -n 1)

  "$A3_ANALYSIS_JAVA_HOME/bin/java" -jar "$JAR_PATH" \
    --input "$project_dir/$bin_dir" \
    --source-root "$project_dir/$src_dir" \
    --output "$OUTPUT_CSV" \
    --include-class "$class_fqn" \
    --fail-on-empty \
    --verbose \
    $append_flag
}

extract_project codec_18_buggy org.apache.commons.codec.binary.StringUtils
extract_project collections_27_buggy org.apache.commons.collections4.map.MultiValueMap --append
extract_project compress_45_buggy org.apache.commons.compress.archivers.tar.TarUtils --append
