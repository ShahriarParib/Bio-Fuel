SHELL := /bin/bash
COMPOSE := docker compose
PY := .venv/bin/python
SERVICES := engine intelligence api

.PHONY: help venv up obs-up chaos-up chaos-down chaos down down-v ps logs smoke test test-common lint fixtures fe-test fe-build sbom k8s-lint k8s-up k8s-demo k8s-ps k8s-down

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

chaos-up: .env ## route the engine through the chaos proxy (F6 malformed drill)
	$(COMPOSE) -f docker-compose.yml -f docker-compose.chaos.yml --profile chaos up -d --build chaos-proxy engine

chaos-down: ## engine back to the direct simulator URL; stop the proxy
	$(COMPOSE) up -d engine
	$(COMPOSE) --profile chaos stop chaos-proxy

chaos: ## run every resilience drill → docs/evidence/resilience.md (takes ~15 min)
	bash scripts/chaos/run_all.sh

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

sbom: ## CycloneDX software bill of materials for every image -> sbom/
	@mkdir -p sbom
	@for s in $(SERVICES); do \
		docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v "$(PWD)/sbom:/out" \
			aquasec/trivy:0.57.1 image --format cyclonedx --output /out/fsip-$$s.cdx.json --no-progress \
			fsip/$$s:$${APP_VERSION:-dev} || exit 1; \
		echo "sbom/fsip-$$s.cdx.json"; \
	done

# ---------------------------------------------------------------- Kubernetes (local kind cluster)
k8s-lint: ## validate the Helm chart (lint + render + kubectl dry-run)
	bash scripts/k8s/lint.sh

k8s-up: ## build images, create the kind cluster, install the chart, wait until healthy
	bash scripts/k8s/up.sh

k8s-demo: ## the two operational proofs: pod self-heal and HPA scale-up -> docs/evidence/kubernetes.md
	bash scripts/k8s/demo.sh

k8s-ps:
	kubectl -n fsip get pods,hpa,svc

k8s-down: ## delete the kind cluster ("make k8s-down keep=1" only uninstalls the release)
	bash scripts/k8s/down.sh $(if $(keep),keep,)
