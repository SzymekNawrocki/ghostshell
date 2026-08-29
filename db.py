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
                CREATE TABLE IF NOT EXISTS scans (
                    id SERIAL PRIMARY KEY,
                    image_name TEXT NOT NULL,
                    high_count INTEGER NOT NULL,
                    critical_count INTEGER NOT NULL,
                    xp_earned INTEGER NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS osint_scans (
                    id SERIAL PRIMARY KEY,
                    tool TEXT NOT NULL,
                    target TEXT NOT NULL,
                    found_count INTEGER NOT NULL,
                    results JSONB NOT NULL,
                    xp_earned INTEGER NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                """
            )
        conn.commit()


def get_total_xp() -> int:
    """Sum of XP across every source (scans + osint_scans) — the number the
    HUD's top bar shows, regardless of which tool earned it."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COALESCE((SELECT SUM(xp_earned) FROM scans), 0)
                    + COALESCE((SELECT SUM(xp_earned) FROM osint_scans), 0);
                """
            )
            return cur.fetchone()[0]
