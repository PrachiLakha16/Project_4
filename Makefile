.PHONY: install lint typecheck test test-cov eval run ingest docker-build helm-lint tf-fmt tf-validate

install:
	pip install -e ".[dev]"

lint:
	ruff check src tests evaluation

format:
	ruff format src tests evaluation

typecheck:
	mypy src

test:
	pytest tests -v

test-cov:
	pytest tests --cov=sre_agent --cov-report=term-missing

eval:
	python -m evaluation.run_eval

run:
	uvicorn sre_agent.api.app:app --reload --port 8000

mcp-server:
	python -m sre_agent.mcp_server.server

ingest:
	python -m sre_agent.rag.ingest --docs-dir docs/runbooks

docker-build:
	docker build -t sre-incident-agent:local .

helm-lint:
	helm lint infrastructure/helm/sre-incident-agent

tf-fmt:
	terraform -chdir=infrastructure/terraform fmt -recursive

tf-validate:
	terraform -chdir=infrastructure/terraform validate

ci: lint typecheck test
