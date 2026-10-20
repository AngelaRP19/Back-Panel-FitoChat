# PostgreSQL 16 + pgvector (imagen oficial de pgvector, Debian bookworm) + PostGIS.
FROM pgvector/pgvector:pg16

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16-postgis-3 \
    && rm -rf /var/lib/apt/lists/*
