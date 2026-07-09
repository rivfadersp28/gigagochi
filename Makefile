.PHONY: check backend-check frontend-check format

check: backend-check frontend-check

backend-check:
	cd backend && .venv/bin/ruff check app tests scripts
	cd backend && .venv/bin/ruff format app tests scripts --check
	cd backend && .venv/bin/pytest -q
	cd backend && .venv/bin/python scripts/export_openapi.py ../frontend/openapi.json --check

frontend-check:
	cd frontend && npm run check

format:
	cd backend && .venv/bin/ruff format app tests
	cd frontend && npm run lint -- --fix
