"""A fatal DuckDB error (for example a full /tmp on the serverless demo) must not poison every later request."""
from __future__ import annotations

import asyncio
import json

import duckdb
from starlette.requests import Request

from pyrosync import api, db


def _request(path: str = "/api/daily") -> Request:
    return Request({"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b"", "server": ("test", 80), "scheme": "http"})


def test_is_fatal_classifies_errors():
    assert db.is_fatal(duckdb.FatalException("FATAL Error: Failed: database has been invalidated because of a previous fatal error."))
    assert db.is_fatal(duckdb.IOException('IO Error: Could not write file "/tmp/x.tmp": No space left on device'))
    assert not db.is_fatal(duckdb.OutOfMemoryException("Out of Memory Error: failed to offload data block"))
    assert not db.is_fatal(ValueError("bad input"))


def test_recover_after_fatal_reopens_a_working_connection(env):
    with db.cursor() as cur:
        assert cur.execute("SELECT 1").fetchone() == (1,)
    spill = db.get_settings().db_path.parent / "duckdb_tmp"
    spill.mkdir(parents=True, exist_ok=True)
    (spill / "leftover.tmp").write_bytes(b"x" * 1024)

    db.recover_after_fatal()

    assert not spill.exists()
    with db.cursor() as cur:  # reopened on demand
        assert cur.execute("SELECT 41 + 1").fetchone() == (42,)


def test_recover_with_reseed_removes_the_database_copy(env):
    db.get_conn()
    path = db.get_settings().db_path
    assert path.exists()

    db.recover_after_fatal(reseed=True)

    assert not path.exists()


def test_api_answers_503_and_resets_after_a_fatal_error(env):
    first = db.get_conn()
    response = asyncio.run(api._server_err(_request(), duckdb.FatalException("FATAL Error: Failed: database has been invalidated")))

    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert "ref" in json.loads(response.body)
    assert db.get_conn() is not first  # a fresh connection, not the invalidated one


def test_api_still_answers_500_for_ordinary_errors(env):
    first = db.get_conn()
    response = asyncio.run(api._server_err(_request(), RuntimeError("boom")))

    assert response.status_code == 500
    assert db.get_conn() is first
