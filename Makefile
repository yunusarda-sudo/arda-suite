.PHONY: test test-jarvis test-memory setup-test-env check-shell

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
	bash -n apps/jarvis/scripts/install_rpi_service.sh
	bash -n services/memory/scripts/install-pi.sh
