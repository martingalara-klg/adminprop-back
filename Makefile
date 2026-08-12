# Atajos de infraestructura local — docs/runbooks/RUNBOOK-LOCAL-001-backend.md
# Issue #2: Docker Compose local (Postgres 16 + Redis 7 + API + workers + Beat).
COMPOSE_FILE := docker/docker-compose.yml
COMPOSE := docker compose -f $(COMPOSE_FILE)

.PHONY: up down logs shell migrate test build ps

## Levanta postgres, redis y api (build incluido). Los workers Celery
## (notification_worker, documents_worker, beat) llegan con el issue #4 y
## se activan aparte con `make up-workers` una vez que existan.
up:
	$(COMPOSE) up --build -d

## Levanta además los workers Celery (profile "workers" — ver
## docker/docker-compose.yml). No funcional hasta el issue #4.
up-workers:
	$(COMPOSE) --profile workers up --build -d

down:
	$(COMPOSE) down

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f $(service)

shell:
	$(COMPOSE) exec api bash

## alembic upgrade head (issue #3 — Alembic + roles PostgreSQL + RLS).
migrate:
	$(COMPOSE) run --rm api alembic upgrade head

test:
	$(COMPOSE) run --rm api pytest

build:
	$(COMPOSE) build
