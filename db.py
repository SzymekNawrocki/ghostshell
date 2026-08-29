import os

import psycopg

DATABASE_URL = os.environ["DATABASE_URL"]


def get_connection():
    return psycopg.connect(DATABASE_URL)


def init_db():
    """Create tables if they don't exist yet. Safe to call on every startup."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS osint_scans (
                    id SERIAL PRIMARY KEY,
                    tool TEXT NOT NULL,
                    target TEXT NOT NULL,
                    found_count INTEGER NOT NULL,
                    results JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                """
            )
        conn.commit()
