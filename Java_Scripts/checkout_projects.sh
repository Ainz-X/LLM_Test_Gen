#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/env.sh"

checkout_if_missing() {
  local project_id="$1"
  local version_id="$2"
  local workspace_dir="$3"
  local target_dir="$A3_ROOT/$workspace_dir"

  if [[ -d "$target_dir" ]]; then
    echo "Checkout exists: $target_dir"
    return 0
  fi

  echo "Checking out $project_id-$version_id into $target_dir"
  "$A3_DEFECTS4J" checkout -p "$project_id" -v "$version_id" -w "$target_dir"
}

checkout_if_missing Codec 18b codec_18_buggy
checkout_if_missing Collections 27b collections_27_buggy
checkout_if_missing Compress 45b compress_45_buggy
