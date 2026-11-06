"""Module 03: ask SQLite about its own schema. Columns, keys, indexes, CHECKs, missing FK indexes.

Run: python 03-introspection-queries/main.py
"""

from __future__ import annotations

import os
import pathlib
import re
import sqlite3
import tempfile
from dataclasses import dataclass, field


def qi(name: str) -> str:
    """Quote an identifier. A table called  we"ird  or  müşteriler  or  select  must not break SQL."""
    return '"' + name.replace('"', '""') + '"'


@dataclass
class Table:
    name: str
    sql: str
    columns: list[tuple[str, str, bool, int, int]] = field(default_factory=list)  # name, type, notnull, pk, hidden
    fks: list[tuple[str, list[str], list[str]]] = field(default_factory=list)  # parent, from_cols, to_cols
    indexes: list[tuple[str, bool, list[str]]] = field(default_factory=list)  # name, unique, columns


def load(con: sqlite3.Connection) -> dict[str, Table]:
    tables: dict[str, Table] = {}
    for name, sql in con.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\'"):
        t = Table(name, sql)
        # table_xinfo (not table_info) also reports generated columns. Pragma functions take bound parameters.
        t.columns = [
            (r[0], r[1], bool(r[2]), r[3], r[4])
            for r in con.execute('SELECT name, type, "notnull", pk, hidden FROM pragma_table_xinfo(?)', (name,))
        ]
        fks: dict[int, tuple[str, list[str], list[str]]] = {}
        for fid, parent, frm, to in con.execute('SELECT id, "table", "from", "to" FROM pragma_foreign_key_list(?) ORDER BY id, seq', (name,)):
            fks.setdefault(fid, (parent, [], []))[1].append(frm)
            fks[fid][2].append(to)
        t.fks = list(fks.values())
        for iname, unique in con.execute('SELECT name, "unique" FROM pragma_index_list(?)', (name,)).fetchall():
            cols = [r[0] for r in con.execute("SELECT name FROM pragma_index_xinfo(?) WHERE key = 1 ORDER BY seqno", (iname,))]
            t.indexes.append((iname, bool(unique), cols))
        tables[name] = t
    return tables


def check_constraints(sql: str) -> list[str]:
    """CHECK (...) bodies. SQLite has no pragma for them: parse the CREATE statement, with balanced parens."""
    out = []
    for m in re.finditer(r"\bCHECK\s*\(", sql, re.I):
        depth, i = 1, m.end()
        while i < len(sql) and depth:
            depth += {"(": 1, ")": -1}.get(sql[i], 0)
            i += 1
        out.append(" ".join(sql[m.end(): i - 1].split()))
    return out


def fk_columns_without_index(tables: dict[str, Table]) -> list[tuple[str, list[str]]]:
    """A FK is 'covered' if some index (or the primary key) STARTS with exactly its columns, in any order."""
    missing = []
    for t in tables.values():
        pk = [c[0] for c in sorted((c for c in t.columns if c[3]), key=lambda c: c[3])]
        for _parent, frm, _to in t.fks:
            want = set(frm)
            leads = [pk] + [cols for _, _, cols in t.indexes]
            if not any(len(ix) >= len(want) and set(ix[: len(want)]) == want for ix in leads):
                missing.append((t.name, frm))
    return missing


# ---- a database with some of everything -------------------------------------------------
tmp = tempfile.mkdtemp()
path = os.path.join(tmp, "shop.db")
c = sqlite3.connect(path)
c.executescript(
    """
    CREATE TABLE customers (id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE, city TEXT);
    CREATE TABLE products (
        sku TEXT PRIMARY KEY,
        price REAL CHECK (price >= 0 AND price < 1000000),
        qty INTEGER NOT NULL DEFAULT 0 CHECK (qty >= 0),
        gross REAL GENERATED ALWAYS AS (price * 1.2) VIRTUAL
    );
    CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(id) ON DELETE CASCADE, placed TEXT);
    CREATE TABLE items (
        order_id INTEGER NOT NULL REFERENCES orders(id),
        sku TEXT NOT NULL REFERENCES products(sku),
        qty INTEGER,
        PRIMARY KEY (order_id, sku)
    ) WITHOUT ROWID;
    CREATE INDEX orders_placed ON orders(placed);
    CREATE TABLE "müşteriler" ("şehir" TEXT, "select" INTEGER);
    CREATE TABLE "we""ird" ("co""l" TEXT);
    INSERT INTO "we""ird" VALUES ('x');
    """
)
c.commit()
c.close()

con = sqlite3.connect(pathlib.Path(path).as_uri() + "?mode=ro", uri=True)
tables = load(con)

# columns, keys, generated columns
cols = {c[0]: c for c in tables["products"].columns}
assert cols["sku"][3] == 1 and cols["qty"][2] is True and cols["gross"][4] == 2, "hidden=2 marks a virtual generated column"
assert [c[0] for c in tables["items"].columns] == ["order_id", "sku", "qty"]
assert [c[3] for c in tables["items"].columns] == [1, 2, 0], "pk is the 1-based position inside a composite key"

# foreign keys
assert tables["orders"].fks == [("customers", ["customer_id"], ["id"])]
assert sorted((p, tuple(f)) for p, f, _ in tables["items"].fks) == [("orders", ("order_id",)), ("products", ("sku",))]

# indexes: the UNIQUE and PRIMARY KEY constraints create automatic ones
assert ("orders_placed", False, ["placed"]) in tables["orders"].indexes
assert any(u and cols == ["email"] for _, u, cols in tables["customers"].indexes)

# CHECK constraints come from the CREATE statement
assert check_constraints(tables["products"].sql) == ["price >= 0 AND price < 1000000", "qty >= 0"]

# the exotic names survive because every identifier is quoted
assert "müşteriler" in tables and 'we"ird' in tables
assert con.execute(f"SELECT count(*) FROM {qi('we' + chr(34) + 'ird')}").fetchone() == (1,)
assert [c[0] for c in tables["müşteriler"].columns] == ["şehir", "select"]

# the missing-index finder: orders.customer_id has none; items.order_id is covered by the PK,
# items.sku is NOT (it is the second PK column), customers/products have no FKs
missing = fk_columns_without_index(tables)
assert sorted((t, tuple(c)) for t, c in missing) == [("items", ("sku",)), ("orders", ("customer_id",))], missing
for t, frm in missing:
    print(f"no index on {t}({', '.join(frm)}) -> CREATE INDEX {qi('idx_' + t + '_' + '_'.join(frm))} ON {qi(t)} ({', '.join(qi(x) for x in frm)});")

# row counts: cheap estimate from ANALYZE, else a count that stops at a cap
assert con.execute("SELECT count(*) FROM sqlite_master WHERE name = 'sqlite_stat1'").fetchone() == (0,), "no ANALYZE yet"
capped = con.execute(f"SELECT count(*) FROM (SELECT 1 FROM {qi('we' + chr(34) + 'ird')} LIMIT 101)").fetchone()[0]
assert capped == 1
print("tables:", sorted(tables))
print("OK")
