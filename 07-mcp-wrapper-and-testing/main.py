"""Module 07: wrap it all in an MCP server (a minimal sqlscope) and test it over real stdio.

Run: python 07-mcp-wrapper-and-testing/main.py              (starts the server and attacks it over stdio)
     python 07-mcp-wrapper-and-testing/main.py --serve      (just the server)
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import hashlib
import os
import pathlib
import re
import sqlite3
import sys
import tempfile
import time
from collections.abc import Callable
from typing import Any, TypeVar

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

HERE = os.path.abspath(__file__)
HEADER = b"SQLite format 3\x00"


class ToolFailure(Exception):
    """Something the agent did wrong or can fix. The message is written for the agent."""


# ---- Module 02: the read-only connection ---------------------------------------------------
ALLOWED = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
SAFE_PRAGMAS = {"table_info", "table_xinfo", "foreign_key_list", "index_list", "index_xinfo", "page_size", "page_count"}


def authorizer(action: int, arg1: Any, arg2: Any, db: Any, src: Any) -> int:
    if action in ALLOWED:
        return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() == "load_extension" else sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and (arg1 or "").lower() in SAFE_PRAGMAS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def open_readonly(path: str, seconds: float = 10.0) -> sqlite3.Connection:
    p = pathlib.Path(path).resolve()
    if not p.is_file():
        raise ToolFailure(f"No such file: {p}")
    head = p.read_bytes()[:100]
    if not head:
        raise ToolFailure(f"{p} is empty (0 bytes), not a database.")
    if not head.startswith(HEADER):
        raise ToolFailure(f"{p} is not an SQLite database (no 'SQLite format 3' header); it may be encrypted or another format.")
    wal = head[18] == 2 or head[19] == 2
    wal_file = pathlib.Path(str(p) + "-wal")
    immutable = wal and not (wal_file.exists() and wal_file.stat().st_size > 0)  # Module 02: never create -wal/-shm
    con = sqlite3.connect(p.as_uri() + "?mode=ro" + ("&immutable=1" if immutable else ""), uri=True, isolation_level=None, check_same_thread=False)
    con.text_factory = lambda b: b.decode("utf-8", errors="replace")
    con.execute("PRAGMA query_only = ON")
    con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 64 * 1024 * 1024)
    con.set_authorizer(authorizer)
    deadline = time.monotonic() + seconds
    con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 5000)
    return con


def run(path: str, sql: str, max_rows: int = 100) -> tuple[list[str], list[tuple[Any, ...]], bool]:
    word = re.match(r"\s*([A-Za-z]+)", sql)
    if not word or word.group(1).upper() not in ("SELECT", "WITH", "VALUES"):
        raise ToolFailure("Only SELECT / WITH / VALUES queries are allowed. This server is read-only by construction.")
    con = open_readonly(path)
    try:
        cur = con.execute(sql)
        rows = cur.fetchmany(max_rows + 1)
        return [d[0] for d in cur.description], rows[:max_rows], len(rows) > max_rows
    except sqlite3.Warning as e:  # more than one statement
        raise ToolFailure(f"SQLite rejected the SQL: {e}") from e
    except sqlite3.Error as e:
        text = str(e)
        if "authoriz" in text or "readonly" in text:
            raise ToolFailure("Denied: this server is read-only by construction (only SELECT is allowed).") from e
        if "interrupted" in text:
            raise ToolFailure("The query ran longer than 10 s and was interrupted. Add a WHERE clause or LIMIT.") from e
        if "file is not a database" in text:
            raise ToolFailure("The file is not a readable SQLite database (encrypted or corrupt).") from e
        raise ToolFailure(f"SQLite error: {text}") from e
    finally:
        con.close()


def cell(v: Any, n: int = 200) -> str:  # Module 06
    if v is None:
        return "NULL"
    if isinstance(v, bytes):
        return f"<BLOB {len(v)} bytes 0x{v[:16].hex()}{'...' if len(v) > 16 else ''}>"
    s = str(v)
    return s if len(s) <= n else f"{s[:n]}... [{len(s)} chars total]"


# ---- the MCP server --------------------------------------------------------------------------
mcp = MCPServer(
    "sqlscope-mini",
    instructions="Explore an SQLite file, read-only. db_overview first, then query / explain_query.",
)
F = TypeVar("F", bound=Callable[..., Any])


def tool(fn: F) -> F:
    @functools.wraps(fn)
    def wrapper(*a: Any, **k: Any) -> Any:
        try:
            return fn(*a, **k)
        except ToolFailure as e:  # MCPServer only forwards a ToolError's message to the client
            raise ToolError(str(e)) from e

    mcp.tool()(wrapper)
    return fn


@tool
def db_overview(path: str) -> str:
    """Start here. List the tables and views of an SQLite file with their column counts and row counts."""
    cols, rows, _ = run(path, "SELECT type, name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' ORDER BY 1, 2", 10_000)
    con = open_readonly(path)
    try:
        lines = []
        for typ, name in rows:
            ncols = con.execute("SELECT count(*) FROM pragma_table_xinfo(?)", (name,)).fetchone()[0]
            q = '"' + name.replace('"', '""') + '"'
            n = con.execute(f"SELECT count(*) FROM (SELECT 1 FROM {q} LIMIT 100001)").fetchone()[0] if typ == "table" else None
            count = "view" if n is None else (">=100000" if n > 100_000 else str(n))
            lines.append(f"{typ} {name}: {ncols} columns, rows: {count}")
        return "\n".join(lines) or "The database has no tables."
    finally:
        con.close()


@tool
def query(path: str, sql: str, max_rows: int = 100) -> str:
    """Run ONE read-only SELECT / WITH / VALUES statement and return a markdown table (capped at max_rows)."""
    if not 1 <= max_rows <= 1000:
        raise ToolFailure("max_rows must be between 1 and 1000.")
    cols, rows, more = run(path, sql, max_rows)
    out = ["| " + " | ".join(cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    out += ["| " + " | ".join(cell(v).replace("|", "\\|").replace("\n", "\\n") for v in r) + " |" for r in rows]
    out.append(f"\n{len(rows)} row(s)." + (" TRUNCATED: add LIMIT / WHERE or raise max_rows." if more else ""))
    return "\n".join(out)


@tool
def explain_query(path: str, sql: str) -> str:
    """Show the query plan of a SELECT without running it, and flag full table scans."""
    cols, rows, _ = run(path, "SELECT 1", 1)  # validates the file
    con = open_readonly(path)
    try:
        plan = [r[3] for r in con.execute("EXPLAIN QUERY PLAN " + sql)]
    except sqlite3.Error as e:
        raise ToolFailure(f"SQLite error: {e}") from e
    finally:
        con.close()
    notes = [f"FULL TABLE SCAN: {d}" for d in plan if re.match(r"SCAN \S+$", d)]
    return "\n".join(["Plan:"] + [f"  - {d}" for d in plan] + (["", "Findings:"] + [f"  * {n}" for n in notes] if notes else []))


# ---- the test: a real client, a real subprocess, real attacks --------------------------------------
def snapshot(folder: str) -> dict[str, str]:
    return {n: hashlib.sha256(pathlib.Path(folder, n).read_bytes()).hexdigest() for n in sorted(os.listdir(folder))}


async def drive() -> None:
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "app.db")
    c = sqlite3.connect(db)
    c.execute("PRAGMA journal_mode = WAL")
    c.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT, raw BLOB)")
    c.executemany("INSERT INTO notes(body, raw) VALUES (?, ?)", [(f"note {i}", b"\x89PNG" * 10) for i in range(300)])
    c.commit()
    c.close()  # a WAL database with no -wal / -shm left behind
    open(os.path.join(tmp, "empty.db"), "wb").close()
    with open(os.path.join(tmp, "text.db"), "w", encoding="utf-8") as f:
        f.write("not a database " * 20)
    before = snapshot(tmp)

    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    params = StdioServerParameters(command=sys.executable, args=[HERE, "--serve"], env=env)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as s:
        await s.initialize()
        assert {t.name for t in (await s.list_tools()).tools} == {"db_overview", "query", "explain_query"}

        r = await s.call_tool("db_overview", {"path": db})
        assert not r.is_error and "table notes: 3 columns, rows: 300" in r.content[0].text
        r = await s.call_tool("query", {"path": db, "sql": "SELECT id, raw FROM notes", "max_rows": 2})
        assert "<BLOB 40 bytes 0x89504e47" in r.content[0].text and "TRUNCATED" in r.content[0].text
        r = await s.call_tool("explain_query", {"path": db, "sql": "SELECT * FROM notes WHERE body = 'x'"})
        assert "FULL TABLE SCAN" in r.content[0].text

        # the attacks: every one must come back as a readable error, not a crash, and change nothing
        attacks = {
            "DROP TABLE notes": "Only SELECT",
            "DELETE FROM notes": "Only SELECT",
            "SELECT 1; DROP TABLE notes": "one statement",
            "WITH x AS (SELECT 1) DELETE FROM notes": "Denied",
            "SELECT load_extension('x')": "Denied",
            "ATTACH DATABASE ':memory:' AS m": "Only SELECT",
            "VACUUM INTO 'copy.db'": "Only SELECT",
            "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM r) SELECT count(*) FROM r": "interrupted",
            "SELECT zeroblob(2000000000)": "too big",
            "SELECT * FROM missing_table": "no such table",
        }
        for sql, expected in attacks.items():
            r = await s.call_tool("query", {"path": db, "sql": sql})
            assert r.is_error and expected in r.content[0].text, (sql, r.content[0].text)
        for path, expected in [(os.path.join(tmp, "empty.db"), "empty"), (os.path.join(tmp, "text.db"), "not an SQLite"), (os.path.join(tmp, "nope.db"), "No such file")]:
            r = await s.call_tool("db_overview", {"path": path})
            assert r.is_error and expected in r.content[0].text, (path, r.content[0].text)
        r = await s.call_tool("query", {"path": db, "sql": "SELECT 1", "max_rows": 0})
        assert r.is_error and "max_rows must be" in r.content[0].text

    after = snapshot(tmp)
    assert after == before, "a file changed, appeared or vanished"
    print(f"{len(attacks) + 4} hostile calls answered with readable errors; {len(after)} files byte-identical, no -wal/-shm/-journal created")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--serve", action="store_true")
    if ap.parse_args().serve:
        mcp.run()
    else:
        asyncio.run(drive())
        print("OK")
