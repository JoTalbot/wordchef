.PHONY: test acceptance smoke build serve release

test:
	PYTHONPATH=backend:game:prolepsis:prolepsis/vendor python3 -m pytest tests/ -q

acceptance:
	python3 scripts/acceptance_prolepsis.py

smoke:
	python3 scripts/smoke_test.py http://localhost:8000

build:
	cd frontend && npm install && npm run build

serve:
	PYTHONPATH=backend:game:prolepsis:prolepsis/vendor \
		python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000

release:
	./scripts/run_tests.sh
	python3 scripts/smoke_test.py http://localhost:8000 || true
