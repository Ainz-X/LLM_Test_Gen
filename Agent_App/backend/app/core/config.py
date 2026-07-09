from __future__ import annotations

import os
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[2]


def default_project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "LLM_Test_Gen" / "Data").exists():
            return parent
    return APP_DIR


PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT") or default_project_root())
DATA_DIR = PROJECT_ROOT / "LLM_Test_Gen" / "Data"


class Settings:
    app_name = "A3 Agent App"
    api_prefix = "/api"
    secret_key = os.getenv("SECRET_KEY", "change-me-in-production")
    access_token_minutes = int(os.getenv("ACCESS_TOKEN_MINUTES", "1440"))
    database_url = os.getenv("DATABASE_URL", f"sqlite:///{APP_DIR / 'agent_app.db'}")
    storage_dir = Path(os.getenv("STORAGE_DIR", str(APP_DIR / "storage")))
    openai_api_key = os.getenv("OPENAI_API_KEY", "")
    openai_base_url = os.getenv("OPENAI_BASE_URL", "")
    openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    celery_broker_url = os.getenv("CELERY_BROKER_URL", redis_url)
    celery_result_backend = os.getenv("CELERY_RESULT_BACKEND", redis_url)
    enable_java_compile = os.getenv("ENABLE_JAVA_COMPILE", "0") == "1"
    enable_java_coverage = os.getenv("ENABLE_JAVA_COVERAGE", "0") == "1"
    junit_classpath = os.getenv("JUNIT_CLASSPATH", "")
    jacoco_agent_path = os.getenv("JACOCO_AGENT_PATH", "")
    jacoco_cli_path = os.getenv("JACOCO_CLI_PATH", "")
    method_context_extractor_jar = Path(
        os.getenv(
            "METHOD_CONTEXT_EXTRACTOR_JAR",
            str(PROJECT_ROOT / "LLM_Test_Gen" / "Java_Scripts" / "method-context-extractor" / "target" / "method-context-extractor-0.2.0-SNAPSHOT-jar-with-dependencies.jar"),
        )
    )
    compile_timeout_seconds = int(os.getenv("COMPILE_TIMEOUT_SECONDS", "20"))
    test_timeout_seconds = int(os.getenv("TEST_TIMEOUT_SECONDS", "40"))
    context_extract_timeout_seconds = int(os.getenv("CONTEXT_EXTRACT_TIMEOUT_SECONDS", "300"))
    minio_enabled = os.getenv("MINIO_ENABLED", "0") == "1"
    minio_endpoint = os.getenv("MINIO_ENDPOINT", "localhost:9000")
    minio_access_key = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
    minio_secret_key = os.getenv("MINIO_SECRET_KEY", "minioadmin")
    minio_bucket = os.getenv("MINIO_BUCKET", "a3-agent")
    minio_secure = os.getenv("MINIO_SECURE", "0") == "1"
    milvus_uri = os.getenv("MILVUS_URI", "http://localhost:19530")
    knowledge_base_enabled = os.getenv("KNOWLEDGE_BASE_ENABLED", "0") == "1"
    frontend_origins = [
        origin.strip()
        for origin in os.getenv("FRONTEND_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
        if origin.strip()
    ]


settings = Settings()
