#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <top_level_dir_name> <zip_output_path>" >&2
  echo "Example: $0 firstname_lastname_u0000000 /tmp/Lab_3_u0000000.zip" >&2
  exit 2
fi

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
A3_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
TOP_NAME="$1"
ZIP_PATH="$2"
STAGING_PARENT="$A3_ROOT/submission_staging"
STAGING_DIR="$STAGING_PARENT/$TOP_NAME"

rm -rf "$STAGING_DIR"
mkdir -p "$STAGING_DIR"

copy_item() {
  local item="$1"
  rsync -a \
    --exclude '__pycache__/' \
    --exclude '.DS_Store' \
    --exclude '.git/' \
    --exclude '.test_suite/' \
    --exclude '.classes/' \
    --exclude '.classes_testgen/' \
    --exclude '.classes_instrumented/' \
    --exclude 'target/' \
    --exclude 'build/' \
    --exclude 'submission_staging/' \
    --exclude 'coverage.xml' \
    --exclude 'cobertura.ser' \
    --exclude 'all_tests' \
    --exclude 'failing_tests' \
    --exclude 'summary.csv' \
    --exclude 'jacoco.exec' \
    --exclude 'fixed_version_checkouts/' \
    --exclude '*.aux' \
    --exclude '*.log' \
    --exclude '*.out' \
    "$A3_ROOT/$item" "$STAGING_DIR/"
}

copy_item "Lab_3_u0000000.pdf"
copy_item "research_paper.pdf"
copy_item "Lab_3_u0000000.tex"
copy_item "research_paper.tex"
copy_item "README.md"
copy_item "require.md"
copy_item "LLM_Test_Gen"
copy_item "codec_18_buggy"
copy_item "collections_27_buggy"
copy_item "compress_45_buggy"

rm -f "$ZIP_PATH"
(cd "$STAGING_PARENT" && zip -qr "$ZIP_PATH" "$TOP_NAME")
echo "Wrote $ZIP_PATH"
