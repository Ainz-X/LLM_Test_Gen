# LLM Test Generation Workspace for Assignment 3

This workspace is the official A3 implementation root. It keeps the Defects4J checkouts, the extractor, the LLM orchestration pipeline, and the report sources in one place so the final submission can be reproduced from a single directory.

## Current status

- The three required buggy projects are checked out and compile successfully:
  - `codec_18_buggy`
  - `collections_27_buggy`
  - `compress_45_buggy`
- The Java extractor builds successfully.
- `Method_Context.csv` has been generated with 64 focal-method rows.
- `Test_Data.csv` has been expanded to 196 generation/feedback rows, including one coverage-feedback `Collections-27` refinement row and an explicit `Model` column recording `gpt-4o-mini`.
- Prompt templates are zero-shot and pass structural validation.
- `a3_pipeline.py` passes `py_compile`.
- `a3_pipeline.py validate` currently passes against the generated CSVs and templates.
- The baseline LLM run has been executed with `gpt-4o-mini` for all three projects.
- Current baseline, optimized, and evidence summaries are stored in:
  - `LLM_Test_Gen/Data/Test_Data.csv`
  - `LLM_Test_Gen/Data/Coverage_Summary_baseline.csv`
  - `LLM_Test_Gen/Data/Coverage_Summary_final_combined.csv`
  - `LLM_Test_Gen/Data/Coverage_Summary_optimized_broad.csv`
  - `LLM_Test_Gen/Data/Coverage_Summary_final_coverage.csv`
  - `LLM_Test_Gen/Data/Coverage_Summary_bug_evidence.csv`
  - `LLM_Test_Gen/Data/Coverage_Summary_balanced_candidate.csv`
  - `LLM_Test_Gen/Data/Coverage_Summary_balanced_quality.csv`
  - `LLM_Test_Gen/Data/Optimization_Comparison.csv`
  - `LLM_Test_Gen/Data/Failure_Analysis.csv`
  - `LLM_Test_Gen/Data/Feedback_Gain.csv`
  - `LLM_Test_Gen/Data/Generation_Funnel.csv`
  - `LLM_Test_Gen/Data/Prompt_Ablation.csv`
  - `LLM_Test_Gen/Data/Bug_Differential.csv`
  - `LLM_Test_Gen/Data/Balanced_Selected_Units.csv`
  - `Lab_3_u0000000.pdf`
  - `research_paper.pdf`

## Baseline results snapshot

- `Codec-18b / StringUtils`
  - line coverage: `39 / 39 = 100.00%`
  - branch coverage: `19 / 20 = 95.00%`
  - class pass rate: `35 / 52 = 67.31%`
  - bug identified: `yes`
- `Collections-27b / MultiValueMap`
  - line coverage: `61 / 127 = 48.03%`
  - branch coverage: `20 / 50 = 40.00%`
  - class pass rate: `4 / 11 = 36.36%`
  - bug identified: `yes`
- `Compress-45b / TarUtils`
  - line coverage: `150 / 171 = 87.72%`
  - branch coverage: `95 / 108 = 87.96%`
  - class pass rate: `10 / 47 = 21.28%`
  - bug identified: `yes`
- Overall
  - line coverage: `250 / 337 = 74.18%`
  - branch coverage: `134 / 178 = 75.28%`
  - class pass rate: `49 / 110 = 44.55%`
  - method pass rate: `293 / 401 = 73.07%`
  - bugs identified: `3 / 3`

## Optimized results snapshot

The post-baseline optimization separates suite roles for analysis, but the main Task 6 scoring view is a single `final_combined` evaluation suite:

- `final_combined`: includes all compilable rows from `coverage`, `diagnostic-coverage`, and `bug-evidence`, and excludes syntax/compile failures. This is the main Task 6 scoring/evaluation result, not a clean passing-only suite.
- `optimized_broad_coverage`: includes all compilable non-bug generated tests plus the coverage-feedback `Collections-27` refinement row. This maximizes coverage while keeping bug evidence separate.
- `clean_coverage_suite`: includes only passing coverage tests. This gives a pass-oriented quality view.
- `bug_evidence_suite`: includes only the three expected failing bug-trigger tests.
- `balanced_candidate`: keeps all clean coverage and bug-evidence rows, then adds a pruned set of 25 Compress diagnostic rows selected for coverage recovery. This is an auxiliary multi-objective suite, not the main Task 6 suite.
- `balanced_quality`: keeps only clean coverage and bug-evidence rows. This is the highest pass-rate quality view.

Key optimized results:

- `final_combined` main Task 6 result
  - overall line coverage: `294 / 337 = 87.24%`
  - overall branch coverage: `145 / 178 = 81.46%`
  - overall class pass rate: `89 / 114 = 78.07%`
  - overall method pass rate: `307 / 338 = 90.83%`
  - bugs identified: `3 / 3`
- `optimized_broad_coverage`
  - overall line coverage: `270 / 337 = 80.12%`
  - overall branch coverage: `133 / 178 = 74.72%`
  - overall method pass rate: `304 / 332 = 91.57%`
- `Collections-27b / MultiValueMap` in `optimized_broad_coverage`
  - line coverage: `111 / 127 = 87.40%`
  - branch coverage: `43 / 50 = 86.00%`
  - class pass rate: `5 / 11 = 45.45%`
  - method pass rate: `24 / 33 = 72.73%`
- `clean_coverage_suite`
  - class pass rate: `89 / 89 = 100.00%`
  - method pass rate: `258 / 258 = 100.00%`
  - overall line coverage: `259 / 337 = 76.85%`
  - overall branch coverage: `127 / 178 = 71.35%`
  - `Collections-27b` line/branch coverage: `101 / 127 = 79.53%`, `37 / 50 = 74.00%`
- `bug_evidence_suite`
  - bugs identified: `3 / 3`
- `balanced_candidate`
  - overall line coverage: `271 / 337 = 80.42%`
  - overall branch coverage: `134 / 178 = 75.28%`
  - overall class pass rate: `75 / 78 = 96.15%`
  - overall method pass rate: `235 / 238 = 98.74%`
  - bugs identified: `3 / 3`
- `balanced_quality`
  - overall line coverage: `283 / 337 = 83.98%`
  - overall branch coverage: `139 / 178 = 78.09%`
  - overall class pass rate: `89 / 92 = 96.74%`
  - overall method pass rate: `261 / 264 = 98.86%`
  - bugs identified: `3 / 3`

## Extra analysis added after review

These files were added to make the final package easier to audit and to avoid a thin report:

- `Generation_Funnel.csv`: shows how many generated rows compile, enter the final suite, remain clean coverage tests, identify bugs, or are discarded.
- `Failure_Analysis.csv`: groups failures by compile/runtime/oracle/bug-triggered cause with a representative root cause per project.
- `Feedback_Gain.csv`: quantifies baseline-to-final gains. The main gain is `Collections-27b`, which improves by `+56` covered lines and `+23` covered branches; the main quality gain is the repaired Compress oracle suite, which raises pass rate while preserving bug evidence.
- `Prompt_Ablation.csv`: records the implemented prompt/pipeline versions from naive reference to final combined feedback suite.
- `Bug_Differential.csv`: records that each generated bug-evidence test fails on the buggy checkout and reports `Failing tests: 0` when run against the corresponding fixed Defects4J checkout.
- `Balanced_Selected_Units.csv`: lists the 25 diagnostic Compress generation units used by the auxiliary `balanced_candidate` suite.
- `Test_Data_balanced_candidate.csv`: auxiliary evaluation input used to reproduce `Coverage_Summary_balanced_candidate.csv`; the canonical generation/feedback dataset remains `Test_Data.csv`.
- `Java_Scripts/run_fixed_bug_differential.sh`: reproduces `Bug_Differential.csv` by checking out Codec-18f, Collections-27f, and Compress-45f and running the generated bug-evidence suites.

## How to explain the final-suite pass rate

The main Task 6 evaluation suite follows the finalization rule by removing syntax/compile-invalid tests. It also repairs the Compress byte-level oracle tests that previously dominated runtime/oracle failures. It does not silently delete expected bug-trigger failures, because Task 6 asks for bug evidence and pass rate. This is why `final_combined` now keeps strong coverage (`87.24%` line, `81.46%` branch) while reaching `78.07%` class pass rate and `90.83%` method pass rate.

The correct way to present this is not to call `final_combined` a clean suite. It is a compilable scoring/evaluation suite. The pass-oriented and balanced views are reported separately:

- `final_combined`: main scoring/evaluation view for coverage and bug evidence; `87.24%` line, `81.46%` branch, `78.07%` class pass, `3/3` bugs.
- `balanced_candidate`: multi-objective view; `80.42%` line, `75.28%` branch, `96.15%` class pass, `3/3` bugs.
- `balanced_quality`: pass-oriented view; `83.98%` line, `78.09%` branch, `96.74%` class pass, `3/3` bugs.

This is an algorithmic optimization framing: suite roles and selection thresholds let us move along a coverage/pass-rate frontier. Runtime/oracle failures are counted in pass rate and analysed in `Failure_Analysis.csv`; the three expected bug-evidence failures are separated and validated in `Bug_Differential.csv`.

## Important operations already completed

1. Created the A3 workspace structure under `the workspace root` instead of reusing the A2 directory directly.
2. Added a unified environment script in `Java_Scripts/env.sh` to resolve the split toolchain from environment variables or discoverable local installs instead of depending on hard-coded user-specific paths.
3. Checked out the three required Defects4J buggy versions:
   - `Codec-18b`
   - `Collections-27b`
   - `Compress-45b`
4. Built a dedicated Java extractor project that combines:
   - `SootUp` for bytecode/Jimple extraction
   - `JavaParser` for source-level context extraction
5. Generated `Method_Context.csv` with the required fixed columns:
   - `FQN`
   - `Signature`
   - `Jimple Code Representation`
   - `Method Source`
   - `Field Context`
   - `Constructor/Helper Signatures`
   - `Throws/Modifiers`
6. Expanded `Method_Context.csv` into `Test_Data.csv` so each round-0 generation unit carries:
   - `InputPartition`
   - `TargetIntent`
   - `Round`
   - `FeedbackSummary`
7. Added zero-shot prompt templates for:
   - generation
   - repair
   - feedback
8. Implemented formatting and naming rules so generated test classes are deterministic and collision-free.
9. Added compile-repair logic with a hard cap of three iterations.
10. Fixed the pipeline so failed compile attempts do not leave broken `.java` files behind in the managed test directory.
11. Reworked evaluation to use Defects4J external test suites instead of the project’s developer-written tests.
12. Added per-row checkpoint writes during long `generate` and `compile-repair` runs so progress survives interruptions.
13. Added explicit OpenAI client timeouts and disabled automatic retries to avoid tail requests hanging indefinitely.
14. Corrected coverage parsing twice:
    - first, to avoid double-counting method-level and class-level Cobertura line nodes
    - second, to aggregate all Cobertura class elements belonging to the same source file, which is required for `MultiValueMap.java`
15. Repaired the `Collections-27` bug-targeted scenario by aligning it with the official Defects4J trigger test `MultiValueMapTest::testUnsafeDeSerialization`.
16. Added suite-role metadata to `Test_Data.csv`:
    - `Suite Role`
    - `Include In Final Suite`
    - `Failure Type`
    - `Failure Root Cause`
17. Split evaluation into `coverage`, `diagnostic-coverage`, and `bug-evidence` roles so intentional bug failures no longer pollute pass-oriented metrics.
18. Added a coverage-feedback `MultiValueMap` refinement row for uncovered branches in `clear`, `removeMapping`, `containsValue`, `values`, `putAll`, `iterator`, and `ValuesIterator.remove`.
19. Enhanced `prepare-feedback` so future feedback rows include target source excerpts around uncovered and partial-branch lines, not only numeric line lists.
20. Materialized the final combined suite back into each project's `src/test/java/a3_generated` tree so `Saved Path` entries in `Test_Data.csv` point to auditable files.
21. Repaired the `Compress-45` bug-evidence test so it no longer checks outside the 8-byte field and instead fails through the defective `formatLongOctalOrBinaryBytes` fall-through path.
22. Added fixed-version differential validation for all three bug-evidence tests. Each test fails on the buggy checkout and reports zero failing tests on the corresponding fixed checkout.
23. Repaired the remaining `Compress-45` byte-level oracle/runtime tests for octal formatting, binary formatting, checksum, name encoding, parsing, and private helper access. This converted most TarUtils diagnostic failures into passing coverage tests.
24. Added a balanced suite-selection analysis so the report can discuss the coverage/pass-rate trade-off instead of only reporting the highest-coverage suite.

## Why these decisions were made

### 1. Split the Java runtime by responsibility

This codebase cannot be handled correctly with one JDK:

- Defects4J itself expects a newer Java runtime.
- `Codec-18b` still needs an old Java 8 toolchain for successful compilation.
- The extractor stack (`SootUp` + `JavaParser`) needs a newer analysis JDK.

The fix is in `Java_Scripts/env.sh` plus `Java_Scripts/java_shims/java`:

- framework commands use Java 11
- project compilation uses Java 8
- extractor build/run uses Java 17

This is the main environment decision in the repo. Without it, the assignment does not reproduce reliably.

Before sourcing `env.sh` on a new machine, set any toolchain paths that are not discoverable automatically:

```bash
export D4J_HOME=/path/to/defects4j
export A3_BUILD_JAVA_HOME=/path/to/jdk8
export A3_FRAMEWORK_JAVA_HOME=/path/to/jdk11
export A3_ANALYSIS_JAVA_HOME=/path/to/jdk17
export A3_MVN=/path/to/mvn
```

### 2. Keep A2 as reference only

The shortest correct path was to copy the useful logic into A3 and make A3 self-contained. Reusing A2 in-place would have created hidden dependencies and made the final zip harder to audit.

### 3. Separate `Method_Context.csv` and `Test_Data.csv`

The assignment requires one row per focal method in the extractor output. Strategy expansion and later feedback rounds produce multiple records per method. Mixing both concerns into one CSV would violate the task specification and make later analysis harder.

### 4. Restrict extraction to the three exact target classes

An earlier version of the extractor matched by prefix and accidentally included nested helper classes. That inflated the dataset and broke the task boundary. The extractor now matches the class name exactly.

### 5. Use external suites for evaluation

Running plain `defects4j test` or `defects4j coverage` would execute developer-written tests as well. That contaminates pass-rate and coverage metrics for Task 6.

The evaluation phase now stages our generated tests into a Defects4J external suite archive and runs:

- `defects4j test -s <archive>`
- `defects4j coverage -s <archive> -i <instrument_file>`

This keeps the reported results tied only to the tests produced by this pipeline.

### 6. Aggregate coverage by source file, not only by top-level class

Cobertura emits multiple `<class>` elements for `MultiValueMap.java` because the file contains nested classes. Parsing coverage by exact FQN alone undercounts lines and branches for the file. The parser now aggregates all class entries that share the target source filename.

### 7. Checkpoint long batches

Long `generate` and `compile-repair` runs were vulnerable to losing all progress if a late API call stalled. The pipeline now rewrites `Test_Data.csv` after each processed row, so a restart resumes from saved state rather than repeating the whole project batch.

### 8. Separate coverage, diagnostics, and bug evidence

The baseline mixed three different purposes in one suite: tests intended to pass, tests that compile but reveal oracle/runtime problems, and tests that intentionally fail to expose known Defects4J bugs. That made pass rate hard to interpret.

The optimized pipeline adds suite metadata and evaluates explicit role filters:

- `coverage`: clean tests expected to pass
- `diagnostic-coverage`: compilable generated tests that may still fail but can contribute coverage during analysis
- `bug-evidence`: expected failing tests that identify the known bugs
- `discarded`: compile-failed or unusable attempts retained in CSV for traceability but not staged into final suites

This preserves the full audit trail while allowing separate reporting for coverage maximization, pass-oriented quality, and bug identification.

## Layout

- `Data/`
  - `targets.yaml`: canonical configuration for the three required Defects4J targets.
  - `Method_Context.csv`: one row per focal method with extracted context.
  - `Test_Data.csv`: one row per generation unit / feedback round.
  - `instrument_classes/`: per-project coverage instrumentation lists.
  - `Prompts/prompt_template/`: zero-shot generation, repair, and feedback templates.
- `Java_Scripts/`
  - `env.sh`: resolves `D4J_HOME`, the split JDK setup, and the portable Maven binary.
  - `checkout_projects.sh`: checks out the three required buggy project versions.
  - `build_method_context_extractor.sh`: builds the Java extractor JAR.
  - `extract_method_context.sh`: extracts context into `Method_Context.csv`.
  - `bootstrap_a3.sh`: offline bootstrap for checkout, compile, extractor build, and CSV expansion.
  - `build_reports.sh`: compiles the LaTeX report sources.
  - `prepare_submission_zip.sh`: creates a cleaned submission staging directory and zip with the required top-level folder.
  - `java_shims/java`: routes Java calls to the right JDK.
  - `method-context-extractor/`: Maven project that uses SootUp and JavaParser.
- `Python_Scripts/`
  - `a3_pipeline.py`: expands generation units, renders prompts, formats tests, runs compile-repair, evaluates coverage, prepares feedback rounds, and validates artefacts.
  - `a3_agent.py`: controlled tool-calling wrapper that lets an outer LLM planner choose among the existing pipeline tools.
- `Web_Agent/`
  - `backend.py`: lightweight HTTP backend for chat, Java upload, uploaded-source analysis, generated-test export, and agent tool calls.
  - `static/`: browser UI for talking to the agent and uploading Java source files.
- `Agent_App/`
  - deployable FastAPI + React application with login, persisted chat history, uploaded Java file storage, generated test artifacts, tool-call audit records, and agent memory.

## Commands worth auditing

### Offline bootstrap

```bash
source LLM_Test_Gen/Java_Scripts/env.sh
LLM_Test_Gen/Java_Scripts/bootstrap_a3.sh
```

This does five things:

1. checks out the three projects
2. compiles each project once
3. builds the Java extractor
4. writes `Method_Context.csv`
5. expands `Test_Data.csv`

### Validate the current workspace

```bash
python3 LLM_Test_Gen/Python_Scripts/a3_pipeline.py validate \
  --config LLM_Test_Gen/Data/targets.yaml \
  --method-csv LLM_Test_Gen/Data/Method_Context.csv \
  --test-csv LLM_Test_Gen/Data/Test_Data.csv \
  --prompt-dir LLM_Test_Gen/Data/Prompts/prompt_template
```

This checks:

- extractor column completeness
- target-class scoping
- duplicate focal methods
- zero-shot prompt-template structure
- generation-unit naming and round-0 partition expansion
- formatted/runnable test class naming rules
- optional summary CSV consistency

### Run the controlled agent wrapper

The agent wrapper does not replace `a3_pipeline.py`. It exposes the existing deterministic steps as a small tool set and lets an outer LLM planner decide which tool to call next from observed state.

Local structure check without model calls or heavy tools:

```bash
python3 LLM_Test_Gen/Python_Scripts/a3_agent.py \
  --planner scripted \
  --dry-run-tools \
  --goal "inspect the current generation state"
```

LLM-planned run:

```bash
export OPENAI_API_KEY=...
python3 LLM_Test_Gen/Python_Scripts/a3_agent.py \
  --goal "improve Collections-27 branch coverage while preserving deserialize bug evidence" \
  --planner llm \
  --planner-model gpt-4o-mini \
  --inner-model gpt-4o-mini \
  --max-steps 8
```

The agent can call these whitelisted tools:

- `inspect_workspace`
- `validate_workspace`
- `refresh_method_context`
- `expand_generation_units`
- `generate_tests`
- `compile_and_repair`
- `evaluate_suite`
- `prepare_feedback_round`

This is the agentic version of the pipeline: the LLM decides the next tool from compile results, execution diagnostics, coverage gaps, and bug-evidence state, while the underlying tools remain bounded and auditable.

### Run the web agent

```bash
python3 LLM_Test_Gen/Web_Agent/backend.py --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765`.

With `OPENAI_API_KEY` set, the web agent uses the model for conversation and uploaded-file test generation. Without an API key, it still runs in local fallback mode: uploads are parsed, methods are summarized, and JUnit 4 scaffold tests can be exported.

The web agent is broader than a single generate button. It can:

- chat about the current A3 workspace state
- inspect compile, execution, suite-role, and coverage summaries
- validate existing CSV and prompt artefacts
- analyze an uploaded `.java` file for class/method/test-target structure
- generate JUnit 4 tests for uploaded Java source
- explain test partitions and next-step strategy
- list generated uploaded-source test files
- prepare feedback-driven A3 generation rows when needed

### Run the deployable FastAPI + React app

The production-oriented version lives in `LLM_Test_Gen/Agent_App`.

```bash
cd LLM_Test_Gen/Agent_App
copy .env.example .env
docker compose up --build
```

Open `http://127.0.0.1:8080`.

This version stores users, conversations, messages, uploads, generated artifacts, tool-call logs, and long-term memory in a database. Uploaded Java source and generated test files are stored in mounted file storage, with paths and hashes recorded in the database.

### Generate tests with GPT-4o-mini

```bash
python3 LLM_Test_Gen/Python_Scripts/a3_pipeline.py generate \
  --config LLM_Test_Gen/Data/targets.yaml \
  --test-csv LLM_Test_Gen/Data/Test_Data.csv \
  --template LLM_Test_Gen/Data/Prompts/prompt_template/generation.yaml \
  --prompt-dir LLM_Test_Gen/Data/Prompts/generated \
  --model gpt-4o-mini
```

### Compile and repair generated tests

```bash
python3 LLM_Test_Gen/Python_Scripts/a3_pipeline.py compile-repair \
  --config LLM_Test_Gen/Data/targets.yaml \
  --test-csv LLM_Test_Gen/Data/Test_Data.csv \
  --template LLM_Test_Gen/Data/Prompts/prompt_template/repair.yaml \
  --prompt-dir LLM_Test_Gen/Data/Prompts/repair \
  --model gpt-4o-mini
```

### Evaluate only the generated suite

```bash
python3 LLM_Test_Gen/Python_Scripts/a3_pipeline.py evaluate \
  --config LLM_Test_Gen/Data/targets.yaml \
  --test-csv LLM_Test_Gen/Data/Test_Data.csv \
  --summary-csv LLM_Test_Gen/Data/Coverage_Summary.csv
```

This writes an external suite archive under `Data/Generated_Suites/` and records per-project summary fields such as:

- line totals and line coverage
- branch totals and branch coverage
- executed/passed test classes
- executed/passed test methods
- bug evidence for bug-targeted rows

### Evaluate optimized suite roles

Load the shared environment once before running these commands:

```bash
source LLM_Test_Gen/Java_Scripts/env.sh
```

Main Task 6 suite:

```bash
python3 LLM_Test_Gen/Python_Scripts/a3_pipeline.py evaluate \
  --config LLM_Test_Gen/Data/targets.yaml \
  --test-csv LLM_Test_Gen/Data/Test_Data.csv \
  --summary-csv LLM_Test_Gen/Data/Coverage_Summary_final_combined.csv \
  --suite-source a3finalcombined \
  --include-roles coverage,diagnostic-coverage,bug-evidence
```

Coverage analysis suite without intentional bug failures:

```bash
python3 LLM_Test_Gen/Python_Scripts/a3_pipeline.py evaluate \
  --config LLM_Test_Gen/Data/targets.yaml \
  --test-csv LLM_Test_Gen/Data/Test_Data.csv \
  --summary-csv LLM_Test_Gen/Data/Coverage_Summary_optimized_broad.csv \
  --suite-source a3optimizedbroad \
  --include-roles coverage,diagnostic-coverage
```

```bash
python3 LLM_Test_Gen/Python_Scripts/a3_pipeline.py evaluate \
  --config LLM_Test_Gen/Data/targets.yaml \
  --test-csv LLM_Test_Gen/Data/Test_Data.csv \
  --summary-csv LLM_Test_Gen/Data/Coverage_Summary_final_coverage.csv \
  --suite-source a3coverage \
  --include-roles coverage
```

```bash
python3 LLM_Test_Gen/Python_Scripts/a3_pipeline.py evaluate \
  --config LLM_Test_Gen/Data/targets.yaml \
  --test-csv LLM_Test_Gen/Data/Test_Data.csv \
  --summary-csv LLM_Test_Gen/Data/Coverage_Summary_bug_evidence.csv \
  --suite-source a3bugevidence \
  --include-roles bug-evidence
```

### Regenerate fixed-version bug differential

```bash
LLM_Test_Gen/Java_Scripts/run_fixed_bug_differential.sh
```

The script writes `LLM_Test_Gen/Data/Bug_Differential.csv` and checks that each bug-evidence suite reports `Failing tests: 0` on the corresponding fixed Defects4J checkout.

## Important review note

The current checked-in state includes the original baseline, the post-baseline optimization, the fixed-version bug differential, the repaired Compress oracle tests, and the balanced suite-selection analysis. The main Task 6 result is `Coverage_Summary_final_combined.csv`, which reports one compilable final evaluation suite with overall line/branch coverage `87.24% / 81.46%`, class/method pass rate `78.07% / 90.83%`, and `3 / 3` bug identification. The balanced and pass-oriented suites are auxiliary analysis views that explain the trade-off between coverage and pass rate.

## LLM-dependent phases

Generation, repair, and behavior-guided feedback require `OPENAI_API_KEY`. They are intentionally separated from the offline bootstrap so the non-LLM parts of the submission remain reproducible.

## Python dependencies

The pipeline expects:

- `openai`
- `PyYAML`

Install them with:

```bash
python3 -m pip install -r LLM_Test_Gen/requirements.txt
```

## Remaining manual step

- Replace the `u0000000` placeholders with confirmed student IDs before final submission.
- Assemble the final zip with `Java_Scripts/prepare_submission_zip.sh`; the script excludes build outputs, coverage leftovers, local `.git` directories, caches, and LaTeX temporary files.
