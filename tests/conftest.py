import os

import pytest

import db


@pytest.fixture
def conn(tmp_path, monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to an isolated test PostgreSQL database")
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EMBEDDING_MODEL", "")
    monkeypatch.setenv("MODEL", "test/model")
    with db.connect() as connection:
        db.init_schema(connection)
        tables = connection.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'kb_%'").fetchall()
        from psycopg import sql
        connection.execute(sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(sql.SQL(',').join(sql.Identifier(r['tablename']) for r in tables)))
        yield connection
