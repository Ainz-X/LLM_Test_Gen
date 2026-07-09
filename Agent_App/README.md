# A3 Agent App

This is the production-oriented web version of the A3 test-generation agent.

## Architecture

- Frontend: React + Vite workspace for login, SSE chat, drag-and-drop upload, history, artifacts, preview, and zip downloads.
- Backend: FastAPI API with JWT login, per-user storage, chat persistence, uploaded Java analysis, tool calls, generated artifacts, and feedback capture.
- Database: MySQL in Docker Compose for users, conversations, messages, feedback, uploads, artifacts, tool calls, and memories. SQLite remains the no-config local fallback when `DATABASE_URL` is unset.
- Object storage: MinIO stores uploaded Java files and generated tests. MySQL stores metadata, owner, hash, local path, object key, analysis JSON, and generated artifact records.
- Vector store: Milvus is included for an optional knowledge base. Keep it disabled until you have stable project documents, testing conventions, dependency notes, or recurring compile-failure patterns worth retrieving.
- Memory: conversation history is stored in `messages`; stable preferences or project facts are stored in `agent_memories`.

## Why files are not stored directly in the database

For real use, store file bodies in filesystem/object storage and keep metadata in the database. This avoids bloating relational tables, makes downloads simpler, and lets production deployments switch to S3/MinIO without changing the application model.

## Knowledge base recommendation

The core workflow does not need a knowledge base to generate tests from an uploaded Java file: the active source file, its static analysis, conversation state, and tools are enough. Add a knowledge base when the agent must reuse broader context that is not in the current upload, such as project testing rules, framework examples, historical compile errors, team style preferences, or dependency-specific notes.

Do not put every raw uploaded Java file into Milvus by default. Store uploads in MinIO and MySQL first. Index only durable, reusable knowledge into Milvus so retrieval stays precise.

## Local development

Backend:

```bash
cd LLM_Test_Gen/Agent_App/backend
python -m venv .venv
.venv/Scripts/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Frontend:

```bash
cd LLM_Test_Gen/Agent_App/frontend
pnpm install
pnpm dev
```

Open `http://127.0.0.1:5173`.

## Docker deployment

```bash
cd LLM_Test_Gen/Agent_App
docker compose up --build
```

Open `http://127.0.0.1:8080`.

For production, copy `.env.example` to `.env` and set a real `SECRET_KEY`, database password, and `OPENAI_API_KEY` before starting the stack.

For OpenAI-compatible providers such as DeepSeek, set:

```bash
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=your-model-name
```

## Agent capabilities

- Analyze uploaded Java source and identify likely test targets.
- Generate JUnit 4 tests for uploaded Java files.
- Upload single files, multiple `.java` files, browser-selected folders, or `.zip` packages containing Java files.
- Delete uploaded Java files and their generated artifacts when they are no longer relevant.
- List and read previously generated artifacts.
- Preview generated tests online and download one artifact or a zip package grouped by source class.
- Diagnose generated test artifacts using static checks and optional compile logs.
- Repair generated test artifacts and save repaired versions.
- Compile generated tests and run JaCoCo coverage when the backend image has JDK, JUnit, and JaCoCo enabled.
- Persist users, sessions, conversations, messages, uploads, generated artifacts, tool calls, and long-term memories.
- Stream assistant responses to the UI with SSE and collect thumbs up/down feedback for future turns.
- Inspect the existing A3 workspace summaries.
- Validate existing A3 CSV and prompt artifacts.
- Prepare feedback-driven generation rows through the existing A3 tools.

## Agent loop guardrails

The chat runtime normalizes every user message into a canonical intent before tool selection. Tool results are treated only as observations, not as new user instructions. This avoids attention hijacking where a large generated Java file or JSON tool payload pulls the model away from the user's actual request.

Per-turn tool policy also blocks repeated calls with the same arguments and caps risky tools such as `generate_tests`, `repair_artifact`, `read_artifact`, and `run_coverage` to one call per turn. Generated code is stored as an artifact and shown in the preview panel; tool observations contain only IDs, filenames, hashes, diagnostics, and short previews.

## Compilation and coverage

The backend Docker image installs JDK 17, Maven, JUnit 4, Hamcrest, and JaCoCo. Docker Compose enables:

```bash
ENABLE_JAVA_COMPILE=1
ENABLE_JAVA_COVERAGE=1
JUNIT_CLASSPATH=/opt/java-libs/junit-4.13.2.jar:/opt/java-libs/hamcrest-core-1.3.jar
JACOCO_AGENT_PATH=/opt/java-libs/org.jacoco.agent-0.8.12-runtime.jar
JACOCO_CLI_PATH=/opt/java-libs/org.jacoco.cli-0.8.12-nodeps.jar
```

Coverage can still fail for legitimate project reasons: missing third-party dependencies, incomplete folder uploads, tests that do not compile, or source packages that require a full Maven/Gradle project. The agent reports the real compile/test/report stage instead of inventing a percentage.

The deployable app keeps the current A3 pipeline as a tool backend instead of replacing it.
