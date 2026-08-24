# GitHub Engineering Analytics Platform

A backend application that connects to GitHub, ingests repository activity,
and calculates engineering analytics. This project is intended to demonstrate
production-quality backend engineering practices.

## Current Status: Milestone 1 — FastAPI Backend Foundation

This milestone establishes the base FastAPI application: project structure,
configuration, logging, and a `/health` endpoint. No database, GitHub
integration, caching, or background workers exist yet — those arrive in later
milestones.

## Requirements

- Python 3.12+

## Setup

Create and activate a virtual environment, then install the project with dev
dependencies:

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # macOS / Linux

pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and adjust values as needed:

```bash
cp .env.example .env
```

## Running the server

```bash
uvicorn app.main:app --reload
```

The API will be available at http://127.0.0.1:8000, with interactive docs at
http://127.0.0.1:8000/docs and the OpenAPI schema at
http://127.0.0.1:8000/openapi.json.

## Running tests

```bash
pytest
```

## Linting, formatting, and type checking

```bash
ruff check .
ruff format .
mypy app
```

## Project structure

```
app/
├── main.py              # FastAPI app instance and lifespan wiring
├── config.py             # Typed settings loaded from environment
├── api/routes/           # API route modules
│   └── health.py         # GET /health
├── core/logging.py       # Logging configuration
└── schemas/health.py     # Pydantic response models
tests/                     # pytest suite
```
