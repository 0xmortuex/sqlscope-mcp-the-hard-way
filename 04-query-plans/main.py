"""Module 04: read EXPLAIN QUERY PLAN like a person, and turn it into advice.

Run: python 04-query-plans/main.py
"""

from __future__ import annotations

import os
import pathlib
import re
import sqlite3
import tempfile

tmp = tempfile.mkdtemp()
path = os.path.join(tmp, "plans.db")
w = sqlite3.connect(path)
w.executescript(
    """
    CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, city TEXT);
    CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER, placed TEXT, total REAL);
    CREATE INDEX orders_placed ON orders(placed);
    """
)
w.executemany("INSERT INTO customers VALUES (?, ?, ?)", [(i, f"c{i}", f"city{i % 20}") for i in range(1, 501)])
w.executemany("INSERT INTO orders VALUES (?, ?, ?, ?)", [(i, (i % 500) + 1, f"2024-{(i % 12) + 1:02d}-01", i * 1.5) for i in range(1, 5001)])
w.commit()
w.close()


def plan_rows(con: sqlite3.Connection, sql: str) -> list[tuple[int, int, str]]:
    """EXPLAIN QUERY PLAN does NOT run the query. Rows are (id, parent, notused, detail)."""
    return [(r[0], r[1], r[3]) for r in con.execute("EXPLAIN QUERY PLAN " + sql)]


def render(rows: list[tuple[int, int, str]]) -> str:
    """The plan is a tree: each row names its parent. Indent by depth."""
    kids: dict[int, list[tuple[int, str]]] = {}
    for rid, parent, detail in rows:
        kids.setdefault(parent, []).append((rid, detail))
    out: list[str] = []

    def walk(parent: int, depth: int) -> None:
        for rid, detail in kids.get(parent, []):
            out.append("  " * depth + "- " + detail)
            walk(rid, depth + 1)

    walk(0, 0)
    return "\n".join(out)


def findings(rows: list[tuple[int, int, str]]) -> list[str]:
    out = []
    for _, _, d in rows:
        if m := re.match(r"SCAN (?:TABLE )?(\S+)( USING (?:COVERING )?INDEX \S+)?", d):
            if m.group(2) and "COVERING" in m.group(2):
                out.append(f"full INDEX scan of {m.group(1)} (cheaper than the table, still every entry)")
            else:
                out.append(f"FULL TABLE SCAN of {m.group(1)}: every row is read")
        if "USE TEMP B-TREE" in d:
            out.append("TEMP B-TREE: SQLite sorts in a temporary structure; an index in that order avoids it")
        if m := re.match(r"SEARCH (\S+) USING AUTOMATIC (?:COVERING )?INDEX \((.*)\)", d):
            out.append(f"AUTOMATIC INDEX on {m.group(1)}({m.group(2)}): built on every run because none exists")
    return out


ro = sqlite3.connect(pathlib.Path(path).as_uri() + "?mode=ro", uri=True)

# 1. a filter on an unindexed column: a full scan
rows = plan_rows(ro, "SELECT * FROM orders WHERE customer_id = 7")
print(render(rows))
assert any(re.match(r"SCAN (?:TABLE )?orders$", d) for _, _, d in rows)
assert findings(rows) == ["FULL TABLE SCAN of orders: every row is read"]

# 2. a filter on an indexed column: a search
rows = plan_rows(ro, "SELECT * FROM orders WHERE placed = '2024-03-01'")
assert any("SEARCH orders USING INDEX orders_placed" in d for _, _, d in rows)
assert findings(rows) == []

# 3. ORDER BY on an unindexed column: a temp b-tree; on an indexed one: none
assert any("TEMP B-TREE FOR ORDER BY" in f.upper() or "TEMP B-TREE" in f for f in findings(plan_rows(ro, "SELECT * FROM orders ORDER BY total")))
assert not any("TEMP B-TREE" in f for f in findings(plan_rows(ro, "SELECT * FROM orders ORDER BY placed")))

# 4. a join with no index on the join column: SQLite builds one on the fly
rows = plan_rows(ro, "SELECT * FROM customers c JOIN orders o ON o.customer_id = c.id + 0")
f = findings(rows)
print("\n".join(f))
assert any("AUTOMATIC INDEX" in x for x in f) or any("FULL TABLE SCAN" in x for x in f)

# 5. the advice: from the finding to a CREATE INDEX *text*. The server never runs it (it can't: read-only).
con_rw = sqlite3.connect(path)
suggestion = 'CREATE INDEX "idx_orders_customer_id" ON "orders" ("customer_id");'
con_rw.execute(suggestion)
con_rw.commit()
con_rw.close()
after = plan_rows(sqlite3.connect(pathlib.Path(path).as_uri() + "?mode=ro", uri=True), "SELECT * FROM orders WHERE customer_id = 7")
assert any("SEARCH orders USING INDEX idx_orders_customer_id" in d for _, _, d in after)
print("after the index :", render(after))

# 6. EXPLAIN QUERY PLAN never executes: an endless query explains instantly
t = plan_rows(ro, "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM r) SELECT count(*) FROM r")
assert t, "planned without running"

print("OK")
