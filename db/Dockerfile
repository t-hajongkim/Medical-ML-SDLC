FROM postgres:17-bookworm@sha256:3645570cccdfa447589da9f57dd740faa29b30938e861289a5574b6ca6b03826

LABEL org.opencontainers.image.description="Frozen public research splits with patient-overlap and original missingness metadata; no model checkpoint."

ENV POSTGRES_DB=workshop

COPY assets/ /opt/workshop/assets/
COPY db/init.sql /docker-entrypoint-initdb.d/001-workshop.sql
COPY db/healthcheck.sh /usr/local/bin/workshop-db-healthcheck
RUN chmod 0755 /usr/local/bin/workshop-db-healthcheck

HEALTHCHECK --interval=5s --timeout=5s --start-period=30s --retries=12 \
    CMD ["workshop-db-healthcheck"]
