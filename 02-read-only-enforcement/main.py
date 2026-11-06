"""Module 02: read-only by construction. Three layers, each proven by an attack.

Run: python 02-read-only-enforcement/main.py
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import sqlite3
import tempfile
import time


def snapshot(folder: str) -> dict[str, str]:
    """name -> sha256 of every file in a folder. Equal snapshots = nothing changed, nothing created."""
    return {
        n: hashlib.sha256(pathlib.Path(folder, n).read_bytes()).hexdigest()
        for n in sorted(os.listdir(folder))
        if os.path.isfile(os.path.join(folder, n))
    }


def build(path: str, wal: bool = False) -> None:
    c = sqlite3.connect(path)
    if wal:
        c.execute("PRAGMA journal_mode = WAL")
    c.execute("CREATE TABLE t (a INTEGER, b TEXT)")
    c.execute("CREATE INDEX t_a ON t(a)")
    c.executemany("INSERT INTO t VALUES (?, ?)", [(i, f"row {i}") for i in range(100)])
    c.commit()
    c.close()  # the last connection to close a WAL database deletes the -wal and -shm files


# ---- layer 1: the URI. mode=ro --------------------------------------------------------
d1 = tempfile.mkdtemp()
p1 = os.path.join(d1, "plain.db")
build(p1)
ro = sqlite3.connect(pathlib.Path(p1).as_uri() + "?mode=ro", uri=True, isolation_level=None)
for sql in ["INSERT INTO t VALUES (1, 'x')", "DROP TABLE t", "CREATE TABLE evil (a)", "PRAGMA user_version = 5"]:
    try:
        ro.execute(sql)
        raise AssertionError(f"mode=ro let this through: {sql}")
    except sqlite3.OperationalError as e:
        assert "readonly" in str(e)
print("layer 1: mode=ro refuses INSERT / DROP / CREATE / PRAGMA assignment ('attempt to write a readonly database')")

# ... but it is NOT enough. Two holes, both found by testing, not by reading the docs:
before = snapshot(d1)
target = os.path.join(d1, "stolen_copy.db")
ro.execute(f"VACUUM INTO '{target}'")  # a read-only connection happily writes a NEW file
assert os.path.exists(target)
print("hole 1 : VACUUM INTO wrote a new file through a mode=ro connection:", sorted(snapshot(d1)))
os.remove(target)
ro.execute("ATTACH DATABASE ':memory:' AS scratch")  # ATTACH works too: a way to reach other files
ro.execute("CREATE TABLE scratch.x (a)")
print("hole 2 : ATTACH + CREATE TABLE in the attached database succeeded")
ro.close()
assert snapshot(d1) == before

d2 = tempfile.mkdtemp()
p2 = os.path.join(d2, "wal.db")
build(p2, wal=True)
assert os.listdir(d2) == ["wal.db"]
w = sqlite3.connect(pathlib.Path(p2).as_uri() + "?mode=ro", uri=True)
w.execute("SELECT count(*) FROM t").fetchone()
w.close()
created = sorted(set(os.listdir(d2)) - {"wal.db"})
print("hole 3 : opening a WAL database with plain mode=ro created", created)
assert created == ["wal.db-shm", "wal.db-wal"]

# ---- layer 2: the immutable trick for WAL files with no pending WAL -------------------
for n in created:
    os.remove(os.path.join(d2, n))
header = pathlib.Path(p2).read_bytes()[:100]
is_wal = header[18] == 2 or header[19] == 2
wal_file = p2 + "-wal"
pending = os.path.exists(wal_file) and os.path.getsize(wal_file) > 0
assert is_wal and not pending
before = snapshot(d2)
imm = sqlite3.connect(pathlib.Path(p2).as_uri() + "?mode=ro&immutable=1", uri=True)
assert imm.execute("SELECT count(*) FROM t").fetchone() == (100,)
imm.close()
assert snapshot(d2) == before and os.listdir(d2) == ["wal.db"]
print("layer 2: mode=ro&immutable=1 reads the WAL database and creates nothing")
# immutable ignores the WAL, so we only use it when there is no pending WAL. With a live writer:
writer = sqlite3.connect(p2, isolation_level=None)
writer.execute("PRAGMA wal_autocheckpoint = 0")
writer.execute("INSERT INTO t VALUES (1000, 'only in the wal')")
reader = sqlite3.connect(pathlib.Path(p2).as_uri() + "?mode=ro", uri=True)
assert reader.execute("SELECT count(*) FROM t").fetchone() == (101,)  # plain mode=ro sees the WAL
reader.close()
stale = sqlite3.connect(pathlib.Path(p2).as_uri() + "?mode=ro&immutable=1", uri=True)
assert stale.execute("SELECT count(*) FROM t").fetchone() == (100,)  # immutable would miss it
stale.close()
writer.close()
print("         with a pending WAL we must use plain mode=ro, or we would read stale data")

# ---- layer 3: PRAGMA query_only + the authorizer -------------------------------------
ALLOWED = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
SAFE_PRAGMAS = {"table_info", "table_xinfo", "foreign_key_list", "index_list", "index_xinfo", "page_size", "page_count"}
DENIED_FUNCTIONS = {"load_extension", "fts3_tokenizer"}
denied_log: list[str] = []


def authorizer(action, arg1, arg2, dbname, source):  # type: ignore[no-untyped-def]
    if action in ALLOWED:
        if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in DENIED_FUNCTIONS:
            denied_log.append(f"function {arg2}")
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and (arg1 or "").lower() in SAFE_PRAGMAS:
        return sqlite3.SQLITE_OK
    denied_log.append(f"action {action} {arg1} {arg2}")
    return sqlite3.SQLITE_DENY


def open_readonly(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(pathlib.Path(path).as_uri() + "?mode=ro", uri=True, isolation_level=None)
    con.execute("PRAGMA query_only = ON")  # set BEFORE the authorizer, which would deny it
    con.set_authorizer(authorizer)
    return con


ATTACKS = [
    "INSERT INTO t VALUES (1, 'x')",
    "UPDATE t SET b = 'x'",
    "DELETE FROM t",
    "DROP TABLE t",
    "CREATE TABLE evil (a)",
    "CREATE TEMP TABLE evil (a)",
    "CREATE VIEW v AS SELECT 1",
    "ATTACH DATABASE ':memory:' AS m",
    "VACUUM",
    "VACUUM INTO 'copy.db'",
    "PRAGMA writable_schema = ON",
    "PRAGMA query_only = OFF",
    "PRAGMA journal_mode = DELETE",
    "PRAGMA user_version = 99",
    "BEGIN",
    "REINDEX",
    "ANALYZE",
    "WITH x AS (SELECT 1) DELETE FROM t",
    "SELECT load_extension('nothing')",
]
d3 = tempfile.mkdtemp()
p3 = os.path.join(d3, "victim.db")
build(p3)
before = snapshot(d3)
cwd = os.getcwd()
os.chdir(d3)  # so that a VACUUM INTO 'copy.db' that slipped through would land in the snapshot
try:
    guarded = open_readonly(p3)
    for sql in ATTACKS:
        try:
            guarded.execute(sql)
            raise AssertionError(f"survived: {sql}")
        except sqlite3.DatabaseError as e:
            assert "authoriz" in str(e) or "readonly" in str(e), (sql, str(e))
    assert len(denied_log) >= len(ATTACKS) - 2, denied_log  # the authorizer, not luck, did the refusing
    # ... and ordinary reading still works, including the introspection pragmas we need:
    assert guarded.execute("SELECT count(*) FROM t").fetchone() == (100,)
    assert guarded.execute("SELECT name FROM pragma_table_info('t')").fetchall() == [("a",), ("b",)]
    assert guarded.execute("WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM r WHERE n < 5) SELECT sum(n) FROM r").fetchone() == (15,)
    guarded.close()
finally:
    os.chdir(cwd)
assert snapshot(d3) == before, "the attacks changed or created a file"
print(f"layer 3: query_only + authorizer denied all {len(ATTACKS)} attacks; file hashes and folder listing identical")

# ---- layer 4: a deadline, because 'read-only' still means 'can burn CPU forever' ------
deadline = [0.0]


def progress() -> int:
    return 1 if time.monotonic() > deadline[0] else 0  # non-zero aborts the statement


g = open_readonly(p3)
g.set_progress_handler(progress, 1000)  # called every 1000 virtual-machine instructions
deadline[0] = time.monotonic() + 0.3
t0 = time.monotonic()
try:
    g.execute("WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM r) SELECT count(*) FROM r").fetchall()
    raise AssertionError("an endless query finished?")
except sqlite3.OperationalError as e:
    assert "interrupted" in str(e)
assert time.monotonic() - t0 < 10
g.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 64 * 1024 * 1024)  # layer 5: no 2 GB values
try:
    g.execute("SELECT zeroblob(2000000000)").fetchall()
    raise AssertionError("a 2 GB blob was allowed")
except sqlite3.DataError as e:
    assert "too big" in str(e)
g.close()
print("layer 4: progress handler stopped an endless recursive CTE; layer 5: length limit refused a 2 GB blob")

print("OK")
