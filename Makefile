SHELL := /bin/bash
COMPOSE := docker compose
PY := .venv/bin/python
SERVICES := engine intelligence api

.PHONY: help venv up obs-up down down-v ps logs smoke test test-common lint fixtures fe-test fe-build

help:
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

.env:
	cp .env.example .env

venv: ## create local venv with all dev deps
	python3 -m venv .venv && .venv/bin/pip install -q -r requirements-dev.txt && .venv/bin/pip install -q -e libs/fsip_common

up: .env ## build + start core stack
	$(COMPOSE) up -d --build

obs-up: .env ## start core + observability (prometheus, grafana)
	$(COMPOSE) --profile obs up -d --build

down: ## stop stack (keeps data)
	$(COMPOSE) --profile obs down

down-v: ## stop stack and DELETE volumes
	$(COMPOSE) --profile obs down -v

ps:
	$(COMPOSE) --profile obs ps

logs: ## make logs s=engine
	$(COMPOSE) logs -f --tail=200 $(s)

smoke: ## health-check every service
	bash scripts/smoke_test.sh

test: test-common ## run all python unit tests locally
	@for s in $(SERVICES); do echo "== $$s"; (cd services/$$s && ../../.venv/bin/python -m pytest -q) || exit 1; done

test-common:
	cd libs/fsip_common && ../../.venv/bin/python -m pytest -q

lint:
	.venv/bin/ruff check libs services

fixtures: ## capture simulator fixtures (RESETS the simulator)
	bash scripts/capture_fixtures.sh

fe-test:
	cd frontend && npm ci && npm test

fe-build:
	cd frontend && npm ci && npm run build
