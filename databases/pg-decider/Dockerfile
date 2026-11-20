# postgres:<PG_MAJOR> with PL/Python and the decider extension installed.
#   docker build -t pg-decider .
#   docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=<password> pg-decider
#   psql "postgres://postgres:$POSTGRES_PASSWORD@localhost/postgres" -c "CREATE EXTENSION decider CASCADE"
ARG PG_MAJOR=16
FROM public.ecr.aws/docker/library/postgres:${PG_MAJOR}
ARG PG_MAJOR
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-plpython3-${PG_MAJOR} ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY decider.control /usr/share/postgresql/${PG_MAJOR}/extension/
COPY sql/decider--*.sql /usr/share/postgresql/${PG_MAJOR}/extension/
USER postgres
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 \
  CMD pg_isready -U postgres || exit 1
