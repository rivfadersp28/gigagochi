.PHONY: check check-fast backend-check backend-check-fast frontend-check frontend-check-fast format

check: backend-check frontend-check

check-fast: backend-check-fast frontend-check-fast

backend-check: backend-check-fast
	cd backend && .venv/bin/pytest -q

backend-check-fast:
	cd backend && .venv/bin/python scripts/check_dependency_lock.py
	cd backend && .venv/bin/ruff check app tests scripts
	cd backend && .venv/bin/ruff format app tests scripts --check
	cd backend && .venv/bin/python scripts/export_openapi.py ../frontend/openapi.json --check

frontend-check: frontend-check-fast
	cd frontend && npm run test

frontend-check-fast:
	cd frontend && npm run check:fast

format:
	cd backend && .venv/bin/ruff format app tests scripts
	cd frontend && npm run lint -- --fix
