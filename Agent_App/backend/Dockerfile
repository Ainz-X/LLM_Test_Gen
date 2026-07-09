FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PROJECT_ROOT=/app \
    STORAGE_DIR=/app/storage \
    ENABLE_JAVA_COMPILE=1 \
    ENABLE_JAVA_COVERAGE=1 \
    JUNIT_CLASSPATH=/opt/java-libs/junit-4.13.2.jar:/opt/java-libs/hamcrest-core-1.3.jar \
    JACOCO_AGENT_PATH=/opt/java-libs/org.jacoco.agent-0.8.12-runtime.jar \
    JACOCO_CLI_PATH=/opt/java-libs/org.jacoco.cli-0.8.12-nodeps.jar

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends default-jdk-headless maven \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /opt/java-libs \
    && mvn -q dependency:copy -Dartifact=junit:junit:4.13.2 -DoutputDirectory=/opt/java-libs \
    && mvn -q dependency:copy -Dartifact=org.hamcrest:hamcrest-core:1.3 -DoutputDirectory=/opt/java-libs \
    && mvn -q dependency:copy -Dartifact=org.jacoco:org.jacoco.agent:0.8.12:jar:runtime -DoutputDirectory=/opt/java-libs \
    && mvn -q dependency:copy -Dartifact=org.jacoco:org.jacoco.cli:0.8.12:jar:nodeps -DoutputDirectory=/opt/java-libs

COPY LLM_Test_Gen/Agent_App/backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY LLM_Test_Gen/Agent_App/backend/app /app/app
COPY LLM_Test_Gen/Python_Scripts /app/LLM_Test_Gen/Python_Scripts
COPY LLM_Test_Gen/Data /app/LLM_Test_Gen/Data

RUN mkdir -p /app/storage

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
