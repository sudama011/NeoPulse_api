# --- Variables ---
VENV = .venv
PYTHON = $(VENV)/bin/python
PIP = $(VENV)/bin/pip
UVICORN = $(VENV)/bin/uvicorn
BIN = $(VENV)/bin

# Colors for terminal output
BLUE = \033[1;34m
GREEN = \033[1;32m
RESET = \033[0m

# Default target
.DEFAULT_GOAL := help

# Phony targets to avoid conflicts with file names
.PHONY: install run clean

# --- Help Message ---
help:
	@echo "$(BLUE)Makefile commands:$(RESET)"
	@echo "  $(GREEN)install$(RESET)       🛠️  Setup: Creates .venv and installs dependencies"
	@echo "  $(GREEN)run$(RESET)           🚀  Run API: Starts the FastAPI server"
	@echo "  $(GREEN)clean$(RESET)         🧹  Clean: Removes __pycache__ and .pyc files"

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
