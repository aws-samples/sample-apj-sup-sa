# Throwaway image that installs the extension and runs the regression suite.
ARG PG_MAJOR=16
FROM public.ecr.aws/docker/library/postgres:${PG_MAJOR}
ARG PG_MAJOR
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      postgresql-plpython3-${PG_MAJOR} postgresql-server-dev-${PG_MAJOR} make python3 \
 && rm -rf /var/lib/apt/lists/*
COPY . /src
WORKDIR /src
RUN make install && chown -R postgres:postgres /src
USER postgres
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 \
  CMD pg_isready -h /tmp -p "${PGPORT:-5498}" -U postgres || exit 1
CMD ["bash", "test/run.sh"]
