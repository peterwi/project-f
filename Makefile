SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

COMPOSE_FILE := docker/compose.yml
ENV_FILE := config/secrets.env

.PHONY: help
help:
	@echo "Targets:"
	@echo "  make init-host-dirs  # create /data/trading-ops dirs (sudo)"
	@echo "  make up              # start postgres"
	@echo "  make down            # stop postgres"
	@echo "  make logs            # tail postgres logs"
	@echo "  make ps              # show container status"
	@echo "  make health          # pg_isready in container"
	@echo "  make psql            # run 'select 1;' in container"
	@echo "  make migrate         # apply SQL migrations"
	@echo "  make db-tables       # list DB tables"
	@echo "  make backup          # pg_dump to /data (retained)"
	@echo "  make restore-test    # restore into a new DB (non-destructive)"
	@echo "  make restore-fresh-test  # destructive fresh restore proof"
	@echo "  make install-backup-timer # install systemd timer (sudo)"
	@echo "  make universe-import  # import config/universe.csv"
	@echo "  make universe-validate # generate validation report"
	@echo "  make market-fetch     # fetch EOD prices (Stooq)"
	@echo "  make fetch-eod        # alias for market-fetch"
	@echo "  make data-quality     # run data quality gate"
	@echo "  make ledger-report    # write ledger report"
	@echo "  make reconcile-add    # add reconciliation snapshot"
	@echo "  make reconcile-run    # run reconciliation gate"
	@echo "  make reconcile-selftest # proves PASS/FAIL"
	@echo "  make run-ops          # one-command ops gates run"
	@echo "  make policy-validate  # validate config/policy.yml"
	@echo "  make stub-signals     # write stub signals to DB"
	@echo "  make riskguard        # deterministic approve/block"
	@echo "  make ticket           # render ticket for LAST_RUN_ID (or RUN_ID=...)"
	@echo "  make confirm          # submit ticket confirmation (defaults LAST_TICKET_ID)"
	@echo "  make run-0800         # scheduled 08:00 UK pipeline run"
	@echo "  make run-1400         # scheduled 14:00 UK pipeline run (NO refetch)"
	@echo "  make alerts-last      # print last 20 alerts"
	@echo "  make runs-last        # print last 5 runs (DB schema-safe)"
	@echo "  make tickets-last     # print last 5 tickets (DB schema-safe)"

.PHONY: init-host-dirs
init-host-dirs:
	@set -euo pipefail; \
	DATA_DIR="$$(grep -E '^POSTGRES_DATA_DIR=' $(ENV_FILE) | cut -d= -f2-)"; \
	BACKUP_DIR="$$(grep -E '^POSTGRES_BACKUP_DIR=' $(ENV_FILE) | cut -d= -f2- || true)"; \
	ARTIFACTS_DIR="$$(grep -E '^ARTIFACTS_DIR=' $(ENV_FILE) | cut -d= -f2- || true)"; \
	RAW_DIR="$$(grep -E '^DATA_RAW_DIR=' $(ENV_FILE) | cut -d= -f2- || true)"; \
	CLEAN_DIR="$$(grep -E '^DATA_CLEAN_DIR=' $(ENV_FILE) | cut -d= -f2- || true)"; \
	if [[ -z "$$DATA_DIR" ]]; then echo "POSTGRES_DATA_DIR missing in $(ENV_FILE)"; exit 2; fi; \
	echo "Creating $$DATA_DIR (sudo)"; \
	sudo mkdir -p "$$DATA_DIR"; \
	sudo chmod 700 "$$DATA_DIR"
	@if [[ -z "$$BACKUP_DIR" ]]; then BACKUP_DIR="/data/trading-ops/backups/postgres"; fi; \
	if [[ -n "$$BACKUP_DIR" ]]; then \
	  echo "Creating $$BACKUP_DIR (sudo)"; \
	  sudo mkdir -p "$$BACKUP_DIR"; \
	  sudo chown "$$(id -u)":"$$(id -g)" "$$BACKUP_DIR"; \
	  sudo chmod 750 "$$BACKUP_DIR"; \
	fi
	@if [[ -z "$$ARTIFACTS_DIR" ]]; then ARTIFACTS_DIR="/data/trading-ops/artifacts"; fi; \
	echo "Creating $$ARTIFACTS_DIR (sudo)"; \
	sudo mkdir -p "$$ARTIFACTS_DIR/reports" "$$ARTIFACTS_DIR/tickets" "$$ARTIFACTS_DIR/runs"; \
	sudo chown -R "$$(id -u)":"$$(id -g)" "$$ARTIFACTS_DIR"; \
	sudo chmod 750 "$$ARTIFACTS_DIR"
	@if [[ -z "$$RAW_DIR" ]]; then RAW_DIR="/data/trading-ops/data/raw"; fi; \
	echo "Creating $$RAW_DIR (sudo)"; \
	sudo mkdir -p "$$RAW_DIR"; \
	sudo chown -R "$$(id -u)":"$$(id -g)" "$$RAW_DIR"; \
	sudo chmod 750 "$$RAW_DIR"
	@if [[ -z "$$CLEAN_DIR" ]]; then CLEAN_DIR="/data/trading-ops/data/clean"; fi; \
	echo "Creating $$CLEAN_DIR (sudo)"; \
	sudo mkdir -p "$$CLEAN_DIR"; \
	sudo chown -R "$$(id -u)":"$$(id -g)" "$$CLEAN_DIR"; \
	sudo chmod 750 "$$CLEAN_DIR"

.PHONY: up
up:
	@set -euo pipefail; \
	test -f "$(ENV_FILE)" || (echo "Missing $(ENV_FILE). Run: cp config/secrets.env.example config/secrets.env" && exit 2); \
	docker compose -f "$(COMPOSE_FILE)" --env-file "$(ENV_FILE)" up -d

.PHONY: down
down:
	@docker compose -f "$(COMPOSE_FILE)" down

.PHONY: logs
logs:
	@docker compose -f "$(COMPOSE_FILE)" logs -f --tail=200 postgres

.PHONY: ps
ps:
	@docker compose -f "$(COMPOSE_FILE)" ps

.PHONY: health
health:
	@set -euo pipefail; \
	test -f "$(ENV_FILE)" || (echo "Missing $(ENV_FILE). Run: cp config/secrets.env.example config/secrets.env" && exit 2); \
	set -a; source "$(ENV_FILE)"; set +a; \
	docker compose -f "$(COMPOSE_FILE)" --env-file "$(ENV_FILE)" exec -T postgres pg_isready -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"

.PHONY: psql
psql:
	@set -euo pipefail; \
	test -f "$(ENV_FILE)" || (echo "Missing $(ENV_FILE). Run: cp config/secrets.env.example config/secrets.env" && exit 2); \
	set -a; source "$(ENV_FILE)"; set +a; \
	docker compose -f "$(COMPOSE_FILE)" --env-file "$(ENV_FILE)" exec -T postgres psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "select 1;"

.PHONY: migrate
migrate:
	@bash scripts/db_migrate.sh

.PHONY: db-tables
db-tables:
	@set -euo pipefail; \
	test -f "$(ENV_FILE)" || (echo "Missing $(ENV_FILE). Run: cp config/secrets.env.example config/secrets.env" && exit 2); \
	set -a; source "$(ENV_FILE)"; set +a; \
	docker compose -f "$(COMPOSE_FILE)" --env-file "$(ENV_FILE)" exec -T postgres psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -c "\\dt"

.PHONY: backup
backup:
	@chmod +x scripts/db_backup.sh
	@./scripts/db_backup.sh

.PHONY: restore-test
restore-test:
	@set -euo pipefail; \
	chmod +x scripts/db_restore.sh; \
	set -a; source "$(ENV_FILE)"; set +a; \
	RESTORE_DB="$${POSTGRES_DB}_restore_test"; \
	echo "Creating restore test DB: $$RESTORE_DB"; \
	docker compose -f "$(COMPOSE_FILE)" --env-file "$(ENV_FILE)" exec -T postgres psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "drop database if exists $$RESTORE_DB;"; \
	docker compose -f "$(COMPOSE_FILE)" --env-file "$(ENV_FILE)" exec -T postgres psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "create database $$RESTORE_DB;"; \
	RESTORE_CONFIRM=YES ./scripts/db_restore.sh --to-db "$$RESTORE_DB"; \
	docker compose -f "$(COMPOSE_FILE)" --env-file "$(ENV_FILE)" exec -T postgres psql -U "$$POSTGRES_USER" -d "$$RESTORE_DB" -v ON_ERROR_STOP=1 -c "select filename from schema_migrations order by applied_at;"; \
	echo "Dropping restore test DB: $$RESTORE_DB"; \
	docker compose -f "$(COMPOSE_FILE)" --env-file "$(ENV_FILE)" exec -T postgres psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "drop database $$RESTORE_DB;";

.PHONY: restore-fresh-test
restore-fresh-test:
	@set -euo pipefail; \
	chmod +x scripts/db_restore.sh; \
	set -a; source "$(ENV_FILE)"; set +a; \
	if [[ "$${POSTGRES_DATA_DIR}" != /data/* ]]; then echo "Refusing: POSTGRES_DATA_DIR must be under /data"; exit 2; fi; \
	ts="$$(date -u +%Y%m%dT%H%M%SZ)"; \
	BACKUP_DIR="$${POSTGRES_BACKUP_DIR:-/data/trading-ops/backups/postgres}"; \
	backup_file="$$(ls -1 "$$BACKUP_DIR"/*.dump 2>/dev/null | sort | tail -n 1)"; \
	if [[ -z "$$backup_file" ]]; then echo "No backups found in $${POSTGRES_BACKUP_DIR}; run: make backup"; exit 2; fi; \
	echo "Stopping services"; \
	docker compose -f "$(COMPOSE_FILE)" --env-file "$(ENV_FILE)" down; \
	echo "Moving existing data dir aside (sudo): $${POSTGRES_DATA_DIR} -> $${POSTGRES_DATA_DIR}.bak-$$ts"; \
	sudo mv "$${POSTGRES_DATA_DIR}" "$${POSTGRES_DATA_DIR}.bak-$$ts"; \
	sudo mkdir -p "$${POSTGRES_DATA_DIR}"; \
	sudo chmod 700 "$${POSTGRES_DATA_DIR}"; \
	echo "Starting services"; \
	docker compose -f "$(COMPOSE_FILE)" --env-file "$(ENV_FILE)" up -d; \
	echo "Waiting for DB health"; \
	for i in {1..30}; do \
	  if docker compose -f "$(COMPOSE_FILE)" --env-file "$(ENV_FILE)" exec -T postgres pg_isready -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" >/dev/null 2>&1; then \
	    echo "DB is accepting connections"; \
	    break; \
	  fi; \
	  sleep 2; \
	  if [[ $$i -eq 30 ]]; then echo "DB did not become ready in time"; exit 2; fi; \
	done; \
	echo "Restoring from $$backup_file"; \
	RESTORE_CONFIRM=YES ./scripts/db_restore.sh --file "$$backup_file"; \
	echo "Verifying schema_migrations"; \
	docker compose -f "$(COMPOSE_FILE)" --env-file "$(ENV_FILE)" exec -T postgres psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "select filename, applied_at from schema_migrations order by applied_at;";

.PHONY: install-backup-timer
install-backup-timer:
	@set -euo pipefail; \
	sudo cp ops/systemd/trading-ops-db-backup.service /etc/systemd/system/trading-ops-db-backup.service; \
	sudo cp ops/systemd/trading-ops-db-backup.timer /etc/systemd/system/trading-ops-db-backup.timer; \
	sudo systemctl daemon-reload; \
	sudo systemctl enable --now trading-ops-db-backup.timer; \
	sudo systemctl status trading-ops-db-backup.timer --no-pager

.PHONY: universe-import
universe-import:
	@python3 scripts/universe_import.py

.PHONY: universe-validate
universe-validate:
	@python3 scripts/universe_validate.py

.PHONY: market-fetch
market-fetch:
	@python3 scripts/market_fetch_eod.py

.PHONY: fetch-eod
fetch-eod: market-fetch

.PHONY: data-quality
data-quality:
	@chmod +x scripts/data_quality_gate.py
	@python3 scripts/data_quality_gate.py

.PHONY: ledger-report
ledger-report:
	@python3 scripts/ledger_report.py

.PHONY: ledger-baseline
ledger-baseline:
	@set -euo pipefail; \
	if [ -z "$${CASH_GBP:-}" ]; then \
		echo "ERROR: CASH_GBP is required, e.g. make ledger-baseline CASH_GBP=25.88" >&2; \
		exit 2; \
	fi; \
	chmod +x scripts/ledger_baseline_cash.py; \
	python3 scripts/ledger_baseline_cash.py --cash-gbp "$$CASH_GBP"

.PHONY: reconcile-add
reconcile-add:
	@chmod +x scripts/reconcile_snapshot_add.py
	@python3 scripts/reconcile_snapshot_add.py $(filter-out $@,$(MAKECMDGOALS))

.PHONY: reconcile-run
reconcile-run:
	@python3 scripts/reconcile_run.py $(filter-out $@,$(MAKECMDGOALS))

.PHONY: reconcile-selftest
reconcile-selftest:
	@chmod +x scripts/reconcile_selftest.py scripts/reconcile_run.py
	@python3 scripts/reconcile_selftest.py

.PHONY: run-ops
run-ops:
	@chmod +x scripts/run_ops.py
	@python3 scripts/run_ops.py

.PHONY: policy-validate
policy-validate:
	@python3 scripts/policy_validate.py

.PHONY: stub-signals
stub-signals:
	@chmod +x scripts/stub_signals.py
	@python3 scripts/stub_signals.py $(filter-out $@,$(MAKECMDGOALS))

.PHONY: riskguard
riskguard:
	@chmod +x scripts/riskguard_run.py
	@python3 scripts/riskguard_run.py $(filter-out $@,$(MAKECMDGOALS))

.PHONY: ticket
ticket:
	@chmod +x scripts/ticket_render.py
	@set -euo pipefail; \
	if [[ -n "$${RUN_ID:-}" ]]; then \
	  python3 scripts/ticket_render.py --run-id "$$RUN_ID"; \
	else \
	  python3 scripts/ticket_render.py; \
	fi

.PHONY: confirm
confirm:
	@chmod +x scripts/confirmations_submit.py
	@set -euo pipefail; \
	python3 scripts/confirmations_submit.py --ack-no-trade

.PHONY: run-0800
run-0800:
	@python3 scripts/run_scheduled.py --cadence 0800

.PHONY: run-1400
run-1400:
	@python3 scripts/run_scheduled.py --cadence 1400

.PHONY: alerts-last
alerts-last:
	@set -euo pipefail; \
	set -a; source "$(ENV_FILE)"; set +a; \
	docker compose -f "$(COMPOSE_FILE)" --env-file "$(ENV_FILE)" exec -T postgres \
	  psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -v ON_ERROR_STOP=1 -c \
	  "select created_at, alert_type, severity, coalesce(run_id::text,'-') as run_id, coalesce(ticket_id::text,'-') as ticket_id, summary from alerts order by created_at desc limit 20;"; \
	echo ""; \
	echo "Latest alert dir:"; \
	ls -1dt /data/trading-ops/artifacts/alerts/* 2>/dev/null | head -n 1 || true

.PHONY: runs-last
runs-last:
	@set -euo pipefail; \
	set -a; source "$(ENV_FILE)"; set +a; \
	docker compose -f "$(COMPOSE_FILE)" --env-file "$(ENV_FILE)" exec -T postgres \
	  psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -v ON_ERROR_STOP=1 -c \
	  "select run_id, created_at, cadence, status, asof_date, git_commit, coalesce(notes,'') as notes from runs order by created_at desc limit 5;"

.PHONY: tickets-last
tickets-last:
	@set -euo pipefail; \
	set -a; source "$(ENV_FILE)"; set +a; \
	docker compose -f "$(COMPOSE_FILE)" --env-file "$(ENV_FILE)" exec -T postgres \
	  psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -v ON_ERROR_STOP=1 -c \
	  "select t.ticket_id, t.created_at, t.status, t.ticket_type, t.run_id, coalesce((t.rendered_json->>'asof_date')::date, r.asof_date) as asof_date from tickets t join runs r on r.run_id=t.run_id order by t.created_at desc limit 5;"

# Allow passing CLI args via extra MAKECMDGOALS, e.g.:
#   make reconcile-add -- --snapshot-date 2025-12-22 --cash-gbp 25.88 --position AAPL=0.1
# Only enabled when one of these targets is present to avoid masking typos.
PASSTHRU_TARGETS := reconcile-add reconcile-run stub-signals riskguard
ifneq (,$(filter $(PASSTHRU_TARGETS),$(MAKECMDGOALS)))
%:
	@:
endif
