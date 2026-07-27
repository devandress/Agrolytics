#!/bin/bash
set -e

if [ -n "$DATABASE_URL_SYNC" ] && [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
    echo "Waiting for PostgreSQL..."
    # Wait up to 30s for the database, connecting via DATABASE_URL_SYNC so this
    # works against any host (local Compose, Render, Supabase, ...).
    max_attempts=30
    attempt=0
    until python3 -c "import sqlalchemy, os; sqlalchemy.create_engine(os.environ['DATABASE_URL_SYNC']).connect()" 2>/dev/null; do
        attempt=$((attempt + 1))
        if [ $attempt -ge $max_attempts ]; then
            echo "PostgreSQL did not respond in time"
            exit 1
        fi
        sleep 1
    done
    echo "PostgreSQL is ready!"
    echo "Running Alembic migrations..."
    alembic upgrade head

    # Seed the demo user only outside production to avoid shipping known creds.
    if [ "${APP_ENV:-development}" != "production" ]; then
        echo "Initializing database with test user (non-production)..."
        python -m app.init_db
    fi
fi

exec "$@"
