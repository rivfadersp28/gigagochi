.PHONY: check backend-check frontend-check format

check: backend-check frontend-check

backend-check:
	cd backend && .venv/bin/ruff check app tests
	cd backend && .venv/bin/ruff format app tests --check
	cd backend && .venv/bin/pytest -q

frontend-check:
	cd frontend && npm run check

format:
	cd backend && .venv/bin/ruff format app tests
	cd frontend && npm run lint -- --fix
