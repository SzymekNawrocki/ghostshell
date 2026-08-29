import json
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
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS manual_notes (
                    id SERIAL PRIMARY KEY,
                    category TEXT NOT NULL,
                    note TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                """
            )
        conn.commit()


def save_osint_scan(tool: str, target: str, found_count: int, results) -> None:
    """Persist one tool run to the shared history table. `results` is anything
    JSON-serializable (list of hits, dict of metadata, ...)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO osint_scans (tool, target, found_count, results)
                VALUES (%s, %s, %s, %s)
                """,
                (tool, target, found_count, json.dumps(results)),
            )
        conn.commit()


def save_manual_note(category: str, note: str) -> dict:
    """Persist one manual log entry (things no tool can automate: an HTB
    machine, a practiced AD technique, an ISO chapter read, ...). Returns the
    saved row so the caller can render it immediately without a re-query."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO manual_notes (category, note)
                VALUES (%s, %s)
                RETURNING id, category, note, created_at
                """,
                (category, note),
            )
            row = cur.fetchone()
        conn.commit()

    return {
        "id": row[0],
        "category": row[1],
        "note": row[2],
        "created_at": row[3].strftime("%Y-%m-%d %H:%M"),
    }


def get_manual_notes(limit: int = 50) -> list[dict]:
    """Most recent manual log entries, newest first."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, category, note, created_at
                FROM manual_notes
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()

    return [
        {
            "id": r[0],
            "category": r[1],
            "note": r[2],
            "created_at": r[3].strftime("%Y-%m-%d %H:%M"),
        }
        for r in rows
    ]
