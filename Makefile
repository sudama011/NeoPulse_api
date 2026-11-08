VENV = .venv
PY = $(VENV)/bin/python
BIN = $(VENV)/bin

.DEFAULT_GOAL := help
.PHONY: help install run test test-unit lint format db-up db-down migrate migration sync backtest

help:
	@echo "install      create .venv and install all dependencies"
	@echo "db-up        start Postgres (docker compose)"
	@echo "migrate      apply database migrations"
	@echo "migration    create a migration: make migration msg='describe change'"
	@echo "sync         download the Kotak instrument master"
	@echo "run          start the API + trading engine"
	@echo "test         run all tests (integration tests need TEST_DATABASE_URL or local test DB)"
	@echo "lint         flake8 + mypy"
	@echo "format       black + isort"
	@echo "backtest     make backtest symbol=RELIANCE strategy=MACD_VOLUME interval=5 days=59"

install:
	python3 -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -r requirements-dev.txt

run:
	$(BIN)/uvicorn app.main:app --host 0.0.0.0 --port 8000

test:
	$(PY) -m pytest tests

test-unit:
	$(PY) -m pytest tests/unit

lint:
	$(BIN)/flake8 app backtest tests scripts
	$(BIN)/mypy app --ignore-missing-imports

format:
	$(BIN)/black app backtest tests scripts
	$(BIN)/isort app backtest tests scripts

db-up:
	docker compose up -d db

db-down:
	docker compose down

migrate:
	$(BIN)/alembic upgrade head

migration:
	@test -n "$(msg)" || (echo "usage: make migration msg='describe change'" && exit 1)
	$(BIN)/alembic revision --autogenerate -m "$(msg)"

sync:
	$(PY) -m scripts.sync_instruments

symbol ?= RELIANCE
strategy ?= MACD_VOLUME
interval ?= 5
days ?= 59
backtest:
	$(PY) -m backtest.engine --symbol $(symbol) --strategy $(strategy) --interval $(interval) --days $(days)
