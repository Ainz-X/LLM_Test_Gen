#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/env.sh"

EXTRACTOR_DIR="$A3_ROOT/LLM_Test_Gen/Java_Scripts/method-context-extractor"

export JAVA_HOME="$A3_ANALYSIS_JAVA_HOME"
export PATH="$JAVA_HOME/bin:$(dirname "$A3_MVN"):$D4J_HOME/framework/bin:$PATH"

"$A3_MVN" -f "$EXTRACTOR_DIR/pom.xml" -DskipTests package
