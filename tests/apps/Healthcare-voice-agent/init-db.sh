#!/bin/bash
set -e

echo "Initializing database..."

# Run the schema
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -f /docker-entrypoint-initdb.d/sql/schema.sql

# Run all functions
for f in /docker-entrypoint-initdb.d/sql/functions/*.sql; do
    echo "Running $f..."
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -f "$f"
done

# Seed additional specialist data
if [ -f /docker-entrypoint-initdb.d/sql/populate_more_specialists.sql ]; then
    echo "Running /docker-entrypoint-initdb.d/sql/populate_more_specialists.sql..."
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -f /docker-entrypoint-initdb.d/sql/populate_more_specialists.sql
fi

echo "Database initialization complete."
