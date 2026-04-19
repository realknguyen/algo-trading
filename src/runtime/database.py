"""Database initialization helpers for CLI/runtime flows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import create_engine, inspect

from config.settings import get_config
from database.models import create_engine_from_config, init_db


@dataclass
class DatabaseInitResult:
    """Summary of a database initialization operation."""

    database_url: str
    tables: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "database_url": self.database_url,
            "tables": self.tables,
        }


def initialize_database(database_url: Optional[str] = None) -> DatabaseInitResult:
    """Create the configured database schema and return a summary."""
    if database_url:
        engine = create_engine(database_url)
        resolved_url = database_url
    else:
        config = get_config()
        engine = create_engine_from_config(config)
        resolved_url = config.database.url

    try:
        init_db(engine)
        inspector = inspect(engine)
        tables = sorted(inspector.get_table_names())
    finally:
        engine.dispose()

    return DatabaseInitResult(
        database_url=resolved_url,
        tables=tables,
    )
