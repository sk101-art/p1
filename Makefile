# ==============================================================================
# Auto-SRE Platform Makefile
# ==============================================================================

.PHONY: help up down build chaos dataset validate test test-python test-java lint \
        laptop1-run laptop1-stop laptop1-verify

help:
	@echo "Available targets:"
	@echo "  make up          - Start all docker containers and background daemons"
	@echo "  make down        - Tear down all containers and volumes"
	@echo "  make build       - Rebuild all microservice images"
	@echo "  make chaos       - Run a chaos scenario"
	@echo "  make dataset     - Run Drain3 clustering and package ML dataset"
	@echo "  make validate    - Run validate_10.py acceptance gates"
	@echo "  make test        - Run all Python and Java unit tests"
	@echo "  make lint        - Run ruff and mypy linters"
	@echo "  make laptop1-run - Start laptop1 Phase1<->Phase2 integration service"
	@echo "  make laptop1-stop - Stop laptop1 integration service"
	@echo "  make laptop1-verify - Verify frozen Phase2 + integration health"

up:
	bash run.sh

down:
	docker compose down -v --remove-orphans

build:
	docker compose build

chaos:
	python chaos_scenarios.py --once

dataset:
	python phase1_processor.py --reset-drain
	python package_ml_dataset.py

validate:
	python validate_10.py

test: test-python test-java

test-python:
	pytest -v tests/

test-java:
	cd api-gateway && mvn test
	cd auth-service && mvn test
	cd order-service && mvn test
	cd payment-service && mvn test

lint:
	ruff check .
	mypy --ignore-missing-imports chaos_orchestrator.py phase1_processor.py package_ml_dataset.py validate_10.py

# ---- laptop1 Phase1 <-> frozen Phase2 integration targets ------------

laptop1-run:
	powershell -File integration/laptop1/run-laptop1-integration.ps1

laptop1-stop:
	powershell -File integration/laptop1/stop-laptop1-integration.ps1

laptop1-verify:
	powershell -File integration/laptop1/verify-laptop1-integration.ps1
