FROM python:3.11-slim AS builder

# Build-time only: compiler toolchain + GDAL/GEOS/PROJ headers to build rasterio.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gdal-bin \
    libgdal-dev \
    libgeos-dev \
    libproj-dev \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


FROM python:3.11-slim AS runtime

# Runtime-only shared libs (no headers, no compiler). Exact package names are
# tied to this base image's Debian release — bump alongside the base image.
# postgresql-client provides pg_dump, used by app/services/backup.py.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgdal36 \
    libgeos-c1t64 \
    libproj25 \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

ENV GDAL_DATA=/usr/share/gdal
ENV PROJ_LIB=/usr/share/proj
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY --from=builder /install /usr/local
COPY . .

RUN mkdir -p /app/data /app/logs && chmod +x /app/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
