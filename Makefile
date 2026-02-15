# --- Variables ---
VENV = .venv
PYTHON = $(VENV)/bin/python3
PIP = $(VENV)/bin/pip3
UVICORN = $(VENV)/bin/uvicorn
ALEMBIC = $(VENV)/bin/alembic
BIN = $(VENV)/bin

# Colors for terminal output
BLUE = \033[1;34m
GREEN = \033[1;32m
YELLOW = \033[1;33m
RED = \033[1;31m
RESET = \033[0m

# Default target
.DEFAULT_GOAL := help

# Phony targets
.PHONY: install run clean db-init db-migrate db-upgrade db-downgrade sync docker-up docker-down test lint help

# --- Help Message ---
help:
	@echo "$(BLUE)NeoPulse Makefile commands:$(RESET)"
	@echo "  $(GREEN)install$(RESET)         🛠️  Setup: Creates .venv and installs dependencies"
	@echo "  $(GREEN)run$(RESET)             🚀  Run API: Starts the FastAPI server"
	@echo "  $(GREEN)clean$(RESET)           🧹  Clean: Removes cache files"
	@echo ""
	@echo "$(YELLOW)Database & Alembic:$(RESET)"
	@echo "  $(GREEN)db-init$(RESET)         🏗️  Init DB: Runs scripts/init_db.py"
	@echo "  $(GREEN)db-migrate$(RESET)      📝  Migrate: Auto-generates a new migration (Usage: make db-migrate msg='desc')"
	@echo "  $(GREEN)db-upgrade$(RESET)      🔄  Upgrade: Applies all migrations to HEAD"
	@echo "  $(GREEN)db-downgrade$(RESET)    b🔙  Downgrade: Reverts the last migration"
	@echo ""
	@echo "$(YELLOW)Scripts & Ops:$(RESET)"
	@echo "  $(GREEN)sync$(RESET)            📥  Sync: Runs the Morning Drill (Master Data Sync)"
	@echo "  $(GREEN)docker-up$(RESET)       🐳  Docker Up: Starts containers (Postgres, Redis)"
	@echo "  $(GREEN)docker-down$(RESET)     🛑  Docker Down: Stops and removes containers"
	@echo "  $(GREEN)test$(RESET)            🧪  Test: Runs Pytest"

# --- Core Commands ---

install:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	@echo "$(GREEN)✅ Setup complete. Activate with: source .venv/bin/activate$(RESET)"

run:
	$(UVICORN) app.main:app --reload --host 0.0.0.0 --port 8000

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	@echo "$(GREEN)🧹 Cache cleaned.$(RESET)"

# --- Database & Alembic ---

db-init:
	@echo "$(BLUE)Initializing Database...$(RESET)"
	$(PYTHON) scripts/init_db.py

db-migrate:
	@if [ -z "$(msg)" ]; then echo "$(RED)Error: Please provide a message. Usage: make db-migrate msg='your message'$(RESET)"; exit 1; fi
	@echo "$(BLUE)Generating Migration...$(RESET)"
	$(ALEMBIC) revision --autogenerate -m "$(msg)"

db-upgrade:
	@echo "$(BLUE)Applying Migrations...$(RESET)"
	$(ALEMBIC) upgrade head

db-downgrade:
	@echo "$(YELLOW)Downgrading last revision...$(RESET)"
	$(ALEMBIC) downgrade -1

# --- Scripts & Operations ---

sync:
	@echo "$(BLUE)Running Morning Drill (Master Data Sync)...$(RESET)"
	$(PYTHON) scripts/sync_master.py

docker-up:
	@echo "$(BLUE)Starting Docker Services...$(RESET)"
	docker-compose up -d
	@echo "$(GREEN)🐳 Services are up!$(RESET)"

docker-down:
	@echo "$(YELLOW)Stopping Docker Services...$(RESET)"
	docker-compose down

test:
	@echo "$(BLUE)Running Tests...$(RESET)"
	$(PYTHON) -m pytest tests/ -v

# --- Code Quality ---

lint: ## Run all linting checks
	@echo "$(BLUE)🔍 Running linting checks...$(RESET)"
	# We ignore E501 (line too long) because strict 79 chars is annoying
	$(BIN)/flake8 app backtest tests scripts --ignore=E501
	$(BIN)/mypy app backtest tests scripts --ignore-missing-imports
	@echo "$(GREEN)✅ Linting completed$(RESET)"

lint-fix: ## Fix auto-fixable linting issues
	@echo "$(BLUE)🔧 Fixing linting issues...$(RESET)"
	$(BIN)/autopep8 --in-place --recursive --aggressive app backtest tests
	@echo "$(GREEN)✅ Auto-fixable issues resolved$(RESET)"

format: ## Format code with black and isort
	@echo "$(BLUE)🎨 Formatting code...$(RESET)"
	$(BIN)/black app backtest tests scripts
	$(BIN)/isort app backtest tests
	@echo "$(GREEN)✅ Code formatting completed$(RESET)"