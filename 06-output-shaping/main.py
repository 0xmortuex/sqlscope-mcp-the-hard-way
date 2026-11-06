"""Module 06: shape results for a reader with a small context window. Cells, rows, bytes, formats.

Run: python 06-output-shaping/main.py
"""

from __future__ import annotations

import csv
import io
import json
import os
import pathlib
import sqlite3
import tempfile
import time
from collections.abc import Sequence
from typing import Any


def fmt_cell(value: Any, max_chars: int = 200) -> str:
    """One SQLite value as text. BLOBs: size + hex prefix. Long text: cut, with its true length."""
    if value is None:
        return "NULL"
    if isinstance(value, bytes):
        more = "..." if len(value) > 16 else ""
        return f"<BLOB {len(value)} bytes 0x{value[:16].hex()}{more}>"
    text = repr(value) if isinstance(value, float) else str(value)
    if len(text) > max_chars:
        return f"{text[:max_chars]}... [{len(text)} chars total]"
    return text


def md(headers: Sequence[str], rows: Sequence[Sequence[Any]], max_chars: int = 200) -> str:
    def esc(s: str) -> str:
        return s.replace("|", "\\|").replace("\r", "\\n").replace("\n", "\\n")

    lines = ["| " + " | ".join(esc(h) for h in headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines += ["| " + " | ".join(esc(fmt_cell(v, max_chars)) for v in r) + " |" for r in rows]
    return "\n".join(lines)


def to_csv(headers: Sequence[str], rows: Sequence[Sequence[Any]], max_chars: int = 200) -> str:
    buf = io.StringIO()
    wr = csv.writer(buf, lineterminator="\n")
    wr.writerow(headers)
    wr.writerows([[fmt_cell(v, max_chars) for v in r] for r in rows])
    return buf.getvalue().rstrip("\n")


def to_json(headers: Sequence[str], rows: Sequence[Sequence[Any]], max_chars: int = 200) -> str:
    def val(v: Any) -> Any:
        return v if v is None or isinstance(v, (int, float)) and v == v and abs(v) != float("inf") else fmt_cell(v, max_chars)

    return json.dumps([dict(zip(headers, map(val, r), strict=True)) for r in rows], ensure_ascii=False, indent=1)


def fetch_capped(con: sqlite3.Connection, sql: str, max_rows: int) -> tuple[list[str], list[tuple[Any, ...]], bool]:
    """Stream: ask for max_rows + 1 rows. The extra one proves 'there was more' without reading it all."""
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchmany(max_rows + 1)
    cur.close()
    return cols, rows[:max_rows], len(rows) > max_rows


def rows_within_bytes(rows: Sequence[Sequence[Any]], max_bytes: int, max_chars: int) -> int:
    total = 0
    for i, r in enumerate(rows):
        total += sum(len(fmt_cell(v, max_chars)) + 3 for v in r)
        if total > max_bytes:
            return max(i, 1)
    return len(rows)


tmp = tempfile.mkdtemp()
path = os.path.join(tmp, "shape.db")
w = sqlite3.connect(path)
w.execute("CREATE TABLE docs (id INTEGER PRIMARY KEY, title TEXT, body TEXT, thumb BLOB, note TEXT)")
w.executemany(
    "INSERT INTO docs VALUES (?, ?, ?, ?, ?)",
    [(i, f"doc {i}", "word " * 2000, b"\x89PNG\r\n\x1a\n" + bytes(range(256)), "a|b\nc" if i == 1 else None) for i in range(1, 1001)],
)
w.execute("CREATE TABLE big (n INTEGER)")
w.execute("WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM r WHERE n < 1000000) INSERT INTO big SELECT n FROM r")
w.commit()
w.close()
con = sqlite3.connect(pathlib.Path(path).as_uri() + "?mode=ro", uri=True)

# 1. cells: a BLOB is never dumped, long text says how long it really was
cols, rows, more = fetch_capped(con, "SELECT title, body, thumb, note FROM docs", 3)
table = md(cols, rows)
assert "<BLOB 264 bytes 0x89504e470d0a1a0a" in table, "size + hex prefix"
assert "[10000 chars total]" in table and len(table) < 2500, "5000 words became ~200 characters"
assert "a\\|b\\nc" in table, "pipes and newlines cannot break the markdown table"
print(table.splitlines()[2][:140])

# 2. rows: stream max_rows + 1, never fetchall(). A million-row table answers instantly.
t0 = time.monotonic()
cols, rows, more = fetch_capped(con, "SELECT * FROM big", 200)
assert len(rows) == 200 and more is True
assert time.monotonic() - t0 < 2
print(f"SELECT * FROM big -> {len(rows)} rows, truncated={more}, {time.monotonic() - t0:.3f}s")
cols, rows, more = fetch_capped(con, "SELECT * FROM big LIMIT 5", 200)
assert len(rows) == 5 and more is False, "no false 'truncated' notice when everything fit"

# 3. bytes: 200 rows x wide cells can still be a lot of text; cut by size too
cols, rows, more = fetch_capped(con, "SELECT title, body FROM docs", 1000)
keep = rows_within_bytes(rows, 20_000, 200)
assert 0 < keep < len(rows), keep
print(f"1000 rows requested, {keep} fit in 20 KB")

# 4. formats: one result, three renderings
sample = [(1, "x", None, 2.5), (2, 'say "hi", ok', b"\x00\x01", float("inf"))]
heads = ["a", "b", "c", "d"]
assert md(heads, sample).splitlines()[2] == "| 1 | x | NULL | 2.5 |"
rd = list(csv.reader(io.StringIO(to_csv(heads, sample))))
assert rd[2] == ["2", 'say "hi", ok', "<BLOB 2 bytes 0x0001>", "inf"], rd[2]
js = json.loads(to_json(heads, sample))
assert js[0] == {"a": 1, "b": "x", "c": None, "d": 2.5} and js[1]["d"] == "inf", js
print("markdown / csv / json agree on", len(sample), "rows")

# 5. time: a deadline turns 'runs for an hour' into an error the agent can read
deadline = [time.monotonic() + 0.3]
con.set_progress_handler(lambda: 1 if time.monotonic() > deadline[0] else 0, 2000)
try:
    con.execute("SELECT count(*) FROM big a, big b, big c").fetchall()
    raise AssertionError("a billion-billion row join finished?")
except sqlite3.OperationalError as e:
    assert "interrupted" in str(e)
    message = "The query ran longer than 10 s and was interrupted. Add a WHERE clause or LIMIT, or query an indexed column."
con.set_progress_handler(None, 0)
print("timeout message:", message)

print("OK")
