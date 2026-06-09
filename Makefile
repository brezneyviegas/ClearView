# ClearView — one-command local setup.
#   make setup   create venv (python 3.11+), install deps, create .env
#   make run     start the gateway
#   make doctor  probe keys/CLIs/ollama, get setup recommendations
#   make test    run the test suite

# Pick the newest python >= 3.11 available on PATH.
PY := $(shell command -v python3.13 2>/dev/null \
	|| command -v python3.12 2>/dev/null \
	|| command -v python3.11 2>/dev/null \
	|| command -v python3 2>/dev/null)

VENV := .venv
BIN  := $(VENV)/bin

.PHONY: setup run doctor test clean

setup:
	@$(PY) -c 'import sys; ok = sys.version_info >= (3, 11); \
	print(f"Using {sys.executable} (python {sys.version.split()[0]})"); \
	sys.exit(0 if ok else 1)' \
	|| (echo "ERROR: python 3.11+ required. Install it (e.g. brew install python@3.12) and re-run."; exit 1)
	$(PY) -m venv $(VENV)
	$(BIN)/pip install --quiet --upgrade pip
	$(BIN)/pip install --quiet -e ".[dev]"
	@test -f .env || cp .env.example .env
	@echo
	@echo "Setup complete. Next:"
	@echo "  make run         # start the gateway (works with zero providers)"
	@echo "  make doctor      # see which providers this machine can use"

run:
	$(BIN)/uvicorn app.main:app --host 127.0.0.1 --port 8000

doctor:
	$(BIN)/python -m app.doctor

test:
	$(BIN)/python -m pytest -q

clean:
	rm -rf $(VENV)
