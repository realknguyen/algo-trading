"""Tests for database runtime helpers."""

from src.runtime.database import initialize_database


def test_initialize_database_creates_schema_for_sqlite(tmp_path):
    """CLI database initialization should create the expected core tables."""
    database_path = tmp_path / "trading.db"
    result = initialize_database(database_url=f"sqlite:///{database_path}")

    assert database_path.exists()
    assert "orders" in result.tables
    assert "trades" in result.tables
    assert "system_logs" in result.tables
