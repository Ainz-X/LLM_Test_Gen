#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/env.sh"

"$SCRIPT_DIR/checkout_projects.sh"

for project_dir in "$A3_ROOT/codec_18_buggy" "$A3_ROOT/collections_27_buggy" "$A3_ROOT/compress_45_buggy"; do
  echo "Compiling $project_dir"
  "$A3_DEFECTS4J" compile -w "$project_dir"
done

"$SCRIPT_DIR/build_method_context_extractor.sh"
"$SCRIPT_DIR/extract_method_context.sh" "$A3_ROOT/LLM_Test_Gen/Data/Method_Context.csv"

python3 "$A3_ROOT/LLM_Test_Gen/Python_Scripts/a3_pipeline.py" expand-units \
  --config "$A3_ROOT/LLM_Test_Gen/Data/targets.yaml" \
  --method-csv "$A3_ROOT/LLM_Test_Gen/Data/Method_Context.csv" \
  --output "$A3_ROOT/LLM_Test_Gen/Data/Test_Data.csv"
