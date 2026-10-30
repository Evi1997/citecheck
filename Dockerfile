# CiteCheck V0.1: an experimental, offline, rule-based prototype.
#
# Targets:
#   runtime (default)  the application and a small synthetic demo, nothing else
#   test               adds the unit tests and the dev evaluation data
# The bundled test subset (eval/data/test.jsonl) is never copied into any
# image; .dockerignore excludes it.
#
# Building pulls the Python base image once, so it needs network access and
# owner approval. Running needs no network. For a reproducible build, pin the
# base image by digest (see README, "Run it in Docker"):
#   --build-arg PYTHON_IMAGE=python:3.11-slim@sha256:<digest>
ARG PYTHON_IMAGE=python:3.11-slim

FROM ${PYTHON_IMAGE} AS app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# An unprivileged user. Application files stay owned by root, so the running
# process can't change them. Reports go to /tmp (a tmpfs in the run command).
RUN useradd --create-home --uid 10001 citecheck
WORKDIR /app
COPY citecheck/ citecheck/
COPY demo/ demo/

# Unit tests, run with: docker run ... citecheck:0.1-test
FROM app AS test
COPY tests/ tests/
COPY eval/ eval/
USER 10001:10001
CMD ["python", "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-v"]

# The default target: the demo, with reports written to /tmp.
FROM app AS runtime
USER 10001:10001
CMD ["python", "-m", "citecheck", "check", "demo/answer.txt", "demo/docs", "--out", "/tmp/citecheck-report", "--no-color"]
