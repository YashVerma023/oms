# OMP - Operations Management Portal
#
# Two stages so the wheels for pandas/numpy are built once and the runtime
# image carries no compiler.
#
# Python 3.12, not 3.13: numpy below 2.2 crashes on import under 3.13 with
# "cannot convert longdouble infinity to integer", and requirements.txt is
# deliberately loose so pip can pick a matching wheel.

FROM python:3.12-slim AS build

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /wheels

# build-essential is needed only if pip has to compile something; it stays in
# this stage and never reaches the image that runs.
RUN apt-get update \
 && apt-get install --no-install-recommends -y build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip wheel --wheel-dir /wheels -r requirements.txt


FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OMP_ENV=production \
    FLASK_DEBUG=false

# curl is for the container healthcheck; nothing else is added.
RUN apt-get update \
 && apt-get install --no-install-recommends -y curl \
 && rm -rf /var/lib/apt/lists/*

# Runs as a normal user. A web process that can rewrite its own code is a
# much larger blast radius than one that cannot.
RUN useradd --create-home --uid 10001 omp

WORKDIR /app

COPY --from=build /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt \
 && rm -rf /wheels

COPY --chown=omp:omp . .

# Three directories are written at runtime and must be volumes, or an edit to
# the rules is lost the next time the image is replaced:
#   config/  the allocation and strategy tag rules, edited from Admin Controls
#   logs/    omp.log
#   data/    uploaded samples kept for the tests to replay
RUN mkdir -p /app/logs /app/data \
 && chown -R omp:omp /app/logs /app/data /app/config \
 && chmod +x docker/entrypoint.sh

USER omp

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

ENTRYPOINT ["./docker/entrypoint.sh"]
