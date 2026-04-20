FROM python:3.11-slim

LABEL org.opencontainers.image.title="FinTech GOAT - Agent Orchestrator"
LABEL org.opencontainers.image.description="Deliberately vulnerable LangChain orchestrator for NuGuard red-team testing"

# /app is the Python root; orchestrator/ is the package inside it.
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Install all deps from requirements.txt EXCEPT langchain==0.0.300 (pydantic conflict)
RUN grep -v "^langchain==0\." requirements.txt > /tmp/reqs_main.txt \
    && pip install --no-cache-dir -r /tmp/reqs_main.txt
# VULN-05: install deliberately vulnerable LangChain 0.0.300 without its
# pydantic<2 constraint so it co-exists with the modern openai>=1.x SDK.
RUN pip install --no-cache-dir --no-deps langchain==0.0.300

# Copy source into the orchestrator/ package directory so that
# `python -m uvicorn orchestrator.main:app` resolves correctly from /app.
RUN mkdir orchestrator
COPY . ./orchestrator/

EXPOSE 8001

CMD ["python", "-m", "uvicorn", "orchestrator.main:app", "--host", "0.0.0.0", "--port", "8001"]
