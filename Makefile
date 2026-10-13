.PHONY: dev-up dev-down gpu-up demo-up test test-gpu lint licenses asos pipeline bench demo-export

dev-up:
	docker compose -f infra/docker-compose.yml --env-file .env --profile dev up -d --build

dev-down:
	docker compose -f infra/docker-compose.yml --env-file .env --profile dev --profile gpu --profile demo down

gpu-up:
	docker compose -f infra/docker-compose.yml --env-file .env --profile gpu up -d --build

demo-up:
	docker compose -f infra/docker-compose.yml --env-file .env --profile demo up -d --build

test:
	python -m pytest -m "not gpu" -q

test-gpu:
	python -m pytest -m gpu -q

lint:
	ruff check .
	ruff format --check .
	mypy

licenses:
	python -m scripts.check_licenses

asos:
	python -m pipeline.ingest.asos_download

pipeline:
	@echo "pipeline: implemented in sessions wt-data and wt-ml (docs/spec.md, section 15)"

bench:
	@echo "bench: implemented in session wt-ml (benchmarks B1-B4)"

demo-export:
	@echo "demo-export: implemented in session E9 (US-901)"
