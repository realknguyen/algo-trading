PYTHON ?= python
PIP ?= $(PYTHON) -m pip

.PHONY: install install-dev test unit integration compile smoke lint format typecheck verify

install:
	$(PIP) install -e .

install-dev:
	$(PIP) install -e ".[dev]"

test:
	pytest -q

unit:
	pytest -q tests/unit

integration:
	pytest -q tests/integration

compile:
	$(PYTHON) -m compileall -q src adapters algorithms backtesting order_management risk_management trading_logging config database main.py

smoke:
	$(PYTHON) main.py list-strategies
	$(PYTHON) main.py backtest --help > /dev/null
	$(PYTHON) main.py init-db --database-url sqlite:///./.codex-smoke.db > /dev/null
	rm -f ./.codex-smoke.db
	$(PYTHON) main.py paper --help > /dev/null
	$(PYTHON) main.py live --help > /dev/null

lint:
	flake8 src/cli.py src/data src/runtime config trading_logging tests/unit/test_cli.py tests/unit/test_data_fetcher.py tests/unit/test_runtime_database.py tests/unit/test_top_level_runtime.py

format:
	black src adapters algorithms backtesting order_management risk_management trading_logging config tests main.py

typecheck:
	mypy src/cli.py src/data src/runtime config trading_logging

verify: compile smoke test
