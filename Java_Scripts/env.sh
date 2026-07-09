#!/usr/bin/env bash
set -euo pipefail

if [[ -n "${BASH_VERSION:-}" ]]; then
  SCRIPT_SOURCE="${BASH_SOURCE[0]}"
elif [[ -n "${ZSH_VERSION:-}" ]]; then
  SCRIPT_SOURCE="${(%):-%N}"
else
  SCRIPT_SOURCE="$0"
fi

SCRIPT_DIR=$(cd "$(dirname "$SCRIPT_SOURCE")" && pwd)
A3_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)

first_existing_path() {
  local candidate
  for candidate in "$@"; do
    if [[ -n "$candidate" && -e "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

java_home_for() {
  local version="$1"
  if [[ -x /usr/libexec/java_home ]]; then
    /usr/libexec/java_home -v "$version" 2>/dev/null || true
  fi
}

command_path_for() {
  local name="$1"
  command -v "$name" 2>/dev/null || true
}

DEFAULT_D4J_HOME=$(first_existing_path "$A3_ROOT/defects4j" "$A3_ROOT/../defects4j" "$HOME/defects4j" 2>/dev/null || true)
DEFAULT_FRAMEWORK_JAVA_HOME=$(java_home_for 11)
DEFAULT_BUILD_JAVA_HOME=$(java_home_for 1.8)
DEFAULT_ANALYSIS_JAVA_HOME=$(java_home_for 17)
DEFAULT_MVN=$(command_path_for mvn)
JAVA_SHIM_DIR="$A3_ROOT/LLM_Test_Gen/Java_Scripts/java_shims"

export A3_ROOT
export D4J_HOME="${D4J_HOME:-$DEFAULT_D4J_HOME}"
export A3_FRAMEWORK_JAVA_HOME="${A3_FRAMEWORK_JAVA_HOME:-$DEFAULT_FRAMEWORK_JAVA_HOME}"
export A3_BUILD_JAVA_HOME="${A3_BUILD_JAVA_HOME:-$DEFAULT_BUILD_JAVA_HOME}"
export A3_ANALYSIS_JAVA_HOME="${A3_ANALYSIS_JAVA_HOME:-$DEFAULT_ANALYSIS_JAVA_HOME}"
export JAVA_HOME="$A3_BUILD_JAVA_HOME"
export A3_MVN="${A3_MVN:-$DEFAULT_MVN}"
if [[ -z "${A3_DEFECTS4J:-}" && -n "$D4J_HOME" ]]; then
  export A3_DEFECTS4J="$D4J_HOME/framework/bin/defects4j"
else
  export A3_DEFECTS4J="${A3_DEFECTS4J:-}"
fi

PATH_PREFIX="$JAVA_SHIM_DIR"
[[ -n "$JAVA_HOME" ]] && PATH_PREFIX="$PATH_PREFIX:$JAVA_HOME/bin"
[[ -n "$A3_MVN" ]] && PATH_PREFIX="$PATH_PREFIX:$(dirname "$A3_MVN")"
[[ -n "$D4J_HOME" ]] && PATH_PREFIX="$PATH_PREFIX:$D4J_HOME/framework/bin"
export PATH="$PATH_PREFIX:$PATH"

require_path() {
  local name="$1"
  local value="$2"
  if [[ -z "$value" || ! -e "$value" ]]; then
    echo "Missing required path for $name: ${value:-<unset>}" >&2
    echo "Set $name before sourcing env.sh. See README.md for the expected toolchain variables." >&2
    return 1 2>/dev/null || exit 1
  fi
}

require_path D4J_HOME "$D4J_HOME"
require_path A3_FRAMEWORK_JAVA_HOME "$A3_FRAMEWORK_JAVA_HOME"
require_path A3_BUILD_JAVA_HOME "$A3_BUILD_JAVA_HOME"
require_path A3_ANALYSIS_JAVA_HOME "$A3_ANALYSIS_JAVA_HOME"
require_path A3_MVN "$A3_MVN"
require_path A3_DEFECTS4J "$A3_DEFECTS4J"
require_path JAVA_SHIM "$JAVA_SHIM_DIR/java"
