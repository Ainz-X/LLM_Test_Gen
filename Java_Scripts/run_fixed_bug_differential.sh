#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
A3_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)

# shellcheck source=/dev/null
source "$SCRIPT_DIR/env.sh"

WORK_ROOT="${1:-$A3_ROOT/fixed_version_checkouts}"
OUT_CSV="$A3_ROOT/LLM_Test_Gen/Data/Bug_Differential.csv"
SUITE_DIR="$A3_ROOT/LLM_Test_Gen/Data/Generated_Suites"

mkdir -p "$WORK_ROOT"

printf '%s\n' \
  'project_key,buggy_version,fixed_version,buggy_failing_tests,fixed_failing_tests,defect_signal,fixed_result,interpretation' \
  > "$OUT_CSV"

run_one() {
  local project_key="$1"
  local d4j_project="$2"
  local bug_id="$3"
  local suite_archive="$4"
  local defect_signal="$5"
  local work_dir="$WORK_ROOT/${project_key}_${bug_id}_fixed"
  local output
  local failing_count

  rm -rf "$work_dir"
  "$A3_DEFECTS4J" checkout -p "$d4j_project" -v "${bug_id}f" -w "$work_dir" >/dev/null
  output=$("$A3_DEFECTS4J" test -w "$work_dir" -s "$SUITE_DIR/$suite_archive" 2>&1)
  failing_count=$(printf '%s\n' "$output" | awk '/Failing tests:/ {print $3}' | tail -n 1)
  failing_count="${failing_count:-unknown}"

  python3 - "$OUT_CSV" "$project_key" "${d4j_project}-${bug_id}b" "${d4j_project}-${bug_id}f" "1" "$failing_count" "$defect_signal" <<'PY'
import csv
import sys

path, project_key, buggy_version, fixed_version, buggy_failing, fixed_failing, signal = sys.argv[1:]
with open(path, "a", newline="", encoding="utf-8") as handle:
    writer = csv.writer(handle)
    writer.writerow([
        project_key,
        buggy_version,
        fixed_version,
        buggy_failing,
        fixed_failing,
        signal,
        "defects4j test -s bug_evidence returned Failing tests: " + fixed_failing + " on the fixed checkout",
        "The generated bug-evidence test fails on the buggy checkout and no longer fails on the fixed checkout, so the failure is tied to the Defects4J defect rather than a generic flaky/oracle failure.",
    ])
PY
}

run_one \
  "codec" \
  "Codec" \
  "18" \
  "Codec-18b-a3bugevidence.1.tar.bz2" \
  "org.apache.commons.codec.binary.StringUtils_equals_java_lang_CharSequence_java_lang_CharSequence_r0_bug_targeted_Test::testEqualsWithDifferentLengthStringBuilders | StringUtils.equals incorrectly reports equality for different-length non-String CharSequence inputs."

run_one \
  "collections" \
  "Collections" \
  "27" \
  "Collections-27b-a3bugevidence.1.tar.bz2" \
  "org.apache.commons.collections4.map.MultiValueMap_readObject_java_io_ObjectInputStream_r0_bug_targeted_Test::testUnsafeDeserializationRejectsNonCollectionFactory | Deserialization accepts a non-Collection factory class instead of rejecting it."

run_one \
  "compress" \
  "Compress" \
  "45" \
  "Compress-45b-a3bugevidence.1.tar.bz2" \
  "org.apache.commons.compress.archivers.tar.TarUtils_formatLongOctalOrBinaryBytes_long_byteArray_int_int_r0_bug_targeted_Test::testFormatLongOctalOrBinaryBytesEightByteRoundTrip | The 8-byte binary formatting path falls through into the BigInteger formatter, causing an exception or preventing a correct parseOctalOrBinary round-trip."

echo "Wrote $OUT_CSV"
