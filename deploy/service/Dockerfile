# NuGuard remote scan service image.
#
#   docker build -f deploy/service/Dockerfile -t nuguard-service .
#   docker run --read-only --tmpfs /tmp -v nuguard-data:/var/lib/nuguard \
#     -v $PWD/nuguard.yaml:/etc/nuguard/nuguard.yaml:ro -p 8080:8080 nuguard-service
#
# Scanner versions are pinned, downloads are checksum-verified, and the Grype /
# Trivy vulnerability databases are baked in so scans do not depend on DB
# downloads at runtime (rebuild the image to refresh them). Tool versions are
# recorded in /etc/nuguard/tools.json for result provenance.

ARG PYTHON_IMAGE=python:3.12-slim

FROM ${PYTHON_IMAGE} AS build
WORKDIR /src
COPY . .
RUN pip install --no-cache-dir build==1.2.2 && python -m build --wheel --outdir /dist

FROM ${PYTHON_IMAGE}
ARG TARGETARCH=amd64
ARG SEMGREP_VERSION=1.172.0
ARG CHECKOV_VERSION=3.3.9
ARG TRIVY_VERSION=0.76.1
ARG GRYPE_VERSION=0.121.0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NUGUARD_LOG_FORMAT=json \
    NUGUARD_TOOLS_JSON=/etc/nuguard/tools.json \
    GRYPE_DB_CACHE_DIR=/opt/grype-db \
    GRYPE_DB_AUTO_UPDATE=false \
    GRYPE_CHECK_FOR_APP_UPDATE=false \
    TRIVY_CACHE_DIR=/opt/trivy-cache \
    TRIVY_SKIP_DB_UPDATE=true \
    TRIVY_SKIP_JAVA_DB_UPDATE=true

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

# Trivy and Grype: release tarballs verified against the published checksums.
RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) TRIVY_ARCH=Linux-64bit; GRYPE_ARCH=linux_amd64 ;; \
      arm64) TRIVY_ARCH=Linux-ARM64; GRYPE_ARCH=linux_arm64 ;; \
      *) echo "unsupported arch $TARGETARCH"; exit 1 ;; \
    esac; \
    cd /tmp; \
    base=https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}; \
    curl -fsSLO "$base/trivy_${TRIVY_VERSION}_${TRIVY_ARCH}.tar.gz"; \
    curl -fsSLO "$base/trivy_${TRIVY_VERSION}_checksums.txt"; \
    grep " trivy_${TRIVY_VERSION}_${TRIVY_ARCH}.tar.gz$" "trivy_${TRIVY_VERSION}_checksums.txt" | sha256sum -c -; \
    tar -xzf "trivy_${TRIVY_VERSION}_${TRIVY_ARCH}.tar.gz" -C /usr/local/bin trivy; \
    base=https://github.com/anchore/grype/releases/download/v${GRYPE_VERSION}; \
    curl -fsSLO "$base/grype_${GRYPE_VERSION}_${GRYPE_ARCH}.tar.gz"; \
    curl -fsSLO "$base/grype_${GRYPE_VERSION}_checksums.txt"; \
    grep " grype_${GRYPE_VERSION}_${GRYPE_ARCH}.tar.gz$" "grype_${GRYPE_VERSION}_checksums.txt" | sha256sum -c -; \
    tar -xzf "grype_${GRYPE_VERSION}_${GRYPE_ARCH}.tar.gz" -C /usr/local/bin grype; \
    rm -f /tmp/*.tar.gz /tmp/*_checksums.txt

COPY --from=build /dist/*.whl /tmp/
# Expand the wheel glob first: "<glob>[service]" as one word would not match.
RUN set -eu; wheel=$(ls /tmp/nuguard-*.whl); \
    pip install --no-cache-dir "${wheel}[service]"; \
    rm -f /tmp/*.whl

# Semgrep and Checkov are used only as CLIs; separate virtualenvs keep their
# pinned dependencies from conflicting with NuGuard's.
RUN set -eu; \
    python -m venv /opt/semgrep && /opt/semgrep/bin/pip install --no-cache-dir "semgrep==${SEMGREP_VERSION}"; \
    python -m venv /opt/checkov && /opt/checkov/bin/pip install --no-cache-dir "checkov==${CHECKOV_VERSION}"; \
    ln -s /opt/semgrep/bin/semgrep /usr/local/bin/semgrep; \
    ln -s /opt/checkov/bin/checkov /usr/local/bin/checkov

# Bake vulnerability databases; record exact tool versions for provenance.
RUN set -eu; \
    mkdir -p /etc/nuguard "$GRYPE_DB_CACHE_DIR" "$TRIVY_CACHE_DIR"; \
    GRYPE_DB_AUTO_UPDATE=true grype db update; \
    TRIVY_SKIP_DB_UPDATE=false trivy image --download-db-only --cache-dir "$TRIVY_CACHE_DIR"; \
    python - <<'PY'
import json, re, subprocess

def version(cmd):
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    match = re.search(r"\d+\.\d+(?:\.\d+)?", out)
    return match.group(0) if match else None

from importlib.metadata import version as pkg
tools = {
    "nuguard": pkg("nuguard"),
    "semgrep": version(["semgrep", "--version"]),
    "checkov": version(["checkov", "--version"]),
    "trivy": version(["trivy", "--version"]),
    "grype": version(["grype", "version"]),
    "git": version(["git", "--version"]),
}
json.dump(tools, open("/etc/nuguard/tools.json", "w"), indent=2, sort_keys=True)
print(tools)
PY

RUN useradd --system --uid 10001 --home-dir /var/lib/nuguard --create-home nuguard \
 && mkdir -p /var/lib/nuguard/scratch \
 && chown -R nuguard:nuguard /var/lib/nuguard \
 && chmod -R a+rX /opt/grype-db /opt/trivy-cache

USER nuguard
WORKDIR /var/lib/nuguard
VOLUME ["/var/lib/nuguard"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/v1/healthz', timeout=4).status == 200 else 1)"

ENTRYPOINT ["nuguard-service", "--config", "/etc/nuguard/nuguard.yaml"]
