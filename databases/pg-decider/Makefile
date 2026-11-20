# PGXS build for the decider extension (PL/Python only, nothing to compile).
#
#   make install         # copy decider.control + sql/*.sql into the server's extension dir
#   make installcheck    # pg_regress against a running server (start test/mock_decider.py first)
#   make docker-test     # full regression run in a throwaway container

EXTENSION    = decider
DATA         = $(wildcard sql/$(EXTENSION)--*.sql)
DOCS         = README.md
REGRESS      = $(patsubst test/sql/%.sql,%,$(sort $(wildcard test/sql/*.sql)))
REGRESS_OPTS = --inputdir=test --outputdir=test --load-extension=plpython3u
PG_CONFIG   ?= pg_config
PGXS        := $(wildcard $(shell $(PG_CONFIG) --pgxs 2>/dev/null))
ifneq ($(PGXS),)
include $(PGXS)
else ifneq ($(MAKECMDGOALS),docker-test)
$(error No PGXS found via $(PG_CONFIG). Install the PostgreSQL server dev files or pass PG_CONFIG=/path/to/pg_config; 'make docker-test' needs neither)
endif

PG_MAJOR ?= 16

.PHONY: docker-test
docker-test:
	docker build --build-arg PG_MAJOR=$(PG_MAJOR) -t pg-decider-test -f test/Dockerfile .
	docker run --rm pg-decider-test
