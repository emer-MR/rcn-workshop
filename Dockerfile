FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# SpatiaLite usuniete (schema v8, 2026-06-08): zapytania przestrzenne czystym
# Pythonem (shapely + pyproj.Geod). Zostaja libproj/geos/gdal -- wymagane przez
# pyproj / shapely / pyogrio (odczyt GPKG). Brak libsqlite3-mod-spatialite.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libproj-dev \
        proj-data \
        proj-bin \
        libgeos-dev \
        libgdal-dev \
        gdal-bin \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --upgrade pip setuptools wheel && \
    pip install -e .

COPY app ./app
COPY rcn_core ./rcn_core
COPY static ./static
COPY templates ./templates
COPY tools ./tools

RUN mkdir -p /app/data/workspaces

EXPOSE 8000

CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "2", \
     "--timeout-keep-alive", "300", \
     "--limit-max-requests", "10000"]
