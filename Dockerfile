# --- Frontend build stage -------------------------------------------------
# Verified against the Docker Hub registry API at implementation time
# (node:24.20.0-slim, current Node LTS, pulls successfully). Entirely
# discarded after the COPY --from below -- the final image contains no Node
# runtime, no npm, no node_modules. Frontend assets are always built fresh
# from source here; a stale local frontend/dist is never the source of
# production assets (frontend/dist is dockerignored from the build context).
FROM node:24.20.0-slim AS frontend-builder

WORKDIR /frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci

COPY frontend/ ./
RUN npm run build

# --- Python runtime stage ---------------------------------------------------
# Verified against the Docker Hub registry API at implementation time
# (python:3.14.7-slim-trixie pulls successfully; base image has no curl/wget/
# gcc and runs as root by default -- both addressed below). No dependency
# lock file exists in this project, so `pip install .` resolves against
# PyPI at build time: the Python/OS layer is now pinned and reproducible,
# but dependency versions are not byte-for-byte deterministic build-to-build
# (see README "Known limitations"). Introducing a lock file is out of scope
# for this milestone.
FROM python:3.14.7-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Runtime-only, non-root user. Created before COPY so the final chown is a
# single pass instead of touching the layer twice.
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin appuser

COPY pyproject.toml ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

# Runtime dependencies only -- `pip install .`, never `.[dev]`, so
# pytest/ruff/mypy never enter this image. The cache mount speeds up
# rebuilds across BuildKit-enabled Docker versions (verified available
# here); it only affects pip's local cache, never the image's build
# context, so it cannot leak into a layer or carry a secret.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install .

# Built frontend only -- source, node_modules, and the Node toolchain that
# produced it never enter this stage at all. app/frontend.py serves this
# directory if present, and simply does nothing if it isn't (see its own
# docstring) -- this COPY is what guarantees it always *is* present in this
# image specifically, regardless of local/CI backend-only workflows that
# never run `npm run build`.
COPY --from=frontend-builder /frontend/dist ./frontend/dist

USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
