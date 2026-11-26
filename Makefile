.PHONY: help test test-jarvis test-memory setup-test-env check-shell

help:
	@echo "ARDA Suite Makefile Targets:"
	@echo "  make setup-test-env  - Create virtual environments and install dependencies"
	@echo "  make test            - Run all Jarvis and Shared Memory test suites"
	@echo "  make test-jarvis     - Run Jarvis test suite"
	@echo "  make test-memory     - Run Shared Memory test suite"
	@echo "  make check-shell     - Syntax check shell scripts across the project"

JARVIS_PYTHON ?= $(CURDIR)/apps/jarvis/.venv/bin/python
MEMORY_PYTHON ?= $(CURDIR)/services/memory/.venv/bin/python
TEST_PYTHON ?= python3

setup-test-env:
	$(TEST_PYTHON) -m venv apps/jarvis/.venv
	apps/jarvis/.venv/bin/python -m pip install -r apps/jarvis/requirements-rpi.txt
	$(TEST_PYTHON) -m venv services/memory/.venv
	services/memory/.venv/bin/python -m pip install -r services/memory/requirements-dev.txt

test: test-jarvis test-memory

test-jarvis:
	@test -x "$(JARVIS_PYTHON)" || { echo "Missing Jarvis test interpreter: $(JARVIS_PYTHON). Run make setup-test-env or set JARVIS_PYTHON." >&2; exit 2; }
	cd apps/jarvis && "$(JARVIS_PYTHON)" -m pytest -q

test-memory:
	@test -x "$(MEMORY_PYTHON)" || { echo "Missing Shared Memory test interpreter: $(MEMORY_PYTHON). Run make setup-test-env or set MEMORY_PYTHON." >&2; exit 2; }
	cd services/memory && "$(MEMORY_PYTHON)" -m pytest -q

check-shell:
	bash -n apps/jarvis/install.sh
	bash -n apps/jarvis/scripts/*.sh
	bash -n services/memory/scripts/*.sh
	bash -n services/memory/*.sh
	bash -n services/memory/dashboard/*.sh
	sh -n services/memory/bin/*
