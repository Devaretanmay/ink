import json
import sqlite3
from pathlib import Path
from typing import Optional

DEFAULT_DB = str(Path("microloop_ccr.db"))


def microloop_retrieve(hash: str, db_path: str = DEFAULT_DB) -> str:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT original, schema_version FROM ccr_entries WHERE hash = ?",
            (hash,),
        ).fetchone()
        if row is not None:
            return row[0]
        return "Error: Hash not found in CCR"
    finally:
        conn.close()


def microloop_expand(hash: str, index: int, db_path: str = DEFAULT_DB) -> str:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT original, schema_version FROM ccr_entries WHERE hash = ?",
            (hash,),
        ).fetchone()
        if row is None:
            return "Error: Data expired or not found."
        content = row[0]
        try:
            items = json.loads(content)
        except json.JSONDecodeError:
            return "Error: Stored data is not valid JSON."
        if not isinstance(items, list):
            return "Error: Stored data is not a JSON array."
        if index >= len(items):
            return "Error: Invalid index."
        return json.dumps(items[index])
    finally:
        conn.close()
