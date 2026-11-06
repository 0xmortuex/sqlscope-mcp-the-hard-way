"""Module 01: an SQLite database is one file of fixed-size pages. Read its header two ways.

Run: python 01-file-format-via-pragmas/main.py
"""

from __future__ import annotations

import os
import pathlib
import sqlite3
import struct
import tempfile

tmp = tempfile.mkdtemp()
path = os.path.join(tmp, "demo.db")

con = sqlite3.connect(path)
con.executescript(
    """
    PRAGMA user_version = 7;
    PRAGMA application_id = 0x12345678;
    CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT, age INTEGER);
    CREATE INDEX people_name ON people(name);
    """
)
con.executemany("INSERT INTO people(name, age) VALUES (?, ?)", [(f"person {i}", i % 90) for i in range(2000)])
con.commit()
con.execute("DELETE FROM people WHERE id > 1000")  # leaves free pages behind
con.commit()
con.close()

# ---- 1. the raw header: the first 100 bytes of the file ------------------------------
with open(path, "rb") as f:
    header = f.read(100)

assert header[:16] == b"SQLite format 3\x00", "every SQLite file starts with this magic string"
(page_size_raw,) = struct.unpack(">H", header[16:18])
page_size = 65536 if page_size_raw == 1 else page_size_raw
write_version, read_version = header[18], header[19]  # 1 = rollback journal, 2 = WAL
(page_count,) = struct.unpack(">I", header[28:32])
(freelist_pages,) = struct.unpack(">I", header[36:40])
(user_version,) = struct.unpack(">I", header[60:64])
(application_id,) = struct.unpack(">I", header[68:72])
(text_encoding,) = struct.unpack(">I", header[56:60])

print("magic            :", header[:15])
print("page size        :", page_size)
print("page count       :", page_count)
print("free pages       :", freelist_pages)

# ---- 2. the same facts through PRAGMAs ------------------------------------------------
ro = sqlite3.connect(pathlib.Path(path).as_uri() + "?mode=ro", uri=True)
assert ro.execute("PRAGMA page_size").fetchone()[0] == page_size
assert ro.execute("PRAGMA page_count").fetchone()[0] == page_count
assert ro.execute("PRAGMA freelist_count").fetchone()[0] == freelist_pages
assert ro.execute("PRAGMA user_version").fetchone()[0] == user_version == 7
assert ro.execute("PRAGMA application_id").fetchone()[0] == application_id == 0x12345678
assert ro.execute("PRAGMA encoding").fetchone()[0] == "UTF-8" and text_encoding == 1
assert ro.execute("PRAGMA journal_mode").fetchone()[0] == "delete" and (write_version, read_version) == (1, 1)

# ---- 3. the file is exactly page_size * page_count bytes ------------------------------
assert os.path.getsize(path) == page_size * page_count
assert freelist_pages > 0, "deleted rows leave free pages: file size is not data size"
print(f"file is {os.path.getsize(path)} bytes = {page_count} pages x {page_size}; {freelist_pages} are free")

# ---- 4. page 1 holds the schema table; SQLite's own catalog is a table --------------
rows = ro.execute("SELECT type, name, tbl_name FROM sqlite_master ORDER BY type, name").fetchall()
assert ("table", "people", "people") in rows and ("index", "people_name", "people") in rows
print("sqlite_master    :", rows)

# ---- 5. dynamic typing: a column's declared type is only a hint (an "affinity") ------
rw = sqlite3.connect(path)
rw.execute("INSERT INTO people(name, age) VALUES ('mixed', 'not a number')")  # accepted!
rw.execute("INSERT INTO people(name, age) VALUES ('numeric text', '42')")  # converted to 42
rw.commit()
rw.close()
types = dict(ro.execute("SELECT name, typeof(age) FROM people WHERE name IN ('mixed', 'numeric text')").fetchall())
assert types == {"mixed": "text", "numeric text": "integer"}, types
dist = ro.execute("SELECT typeof(age), count(*) FROM people GROUP BY 1 ORDER BY 2 DESC").fetchall()
print("typeof(age)      :", dist, " <- an INTEGER column holding text. Module 05 hunts for this.")
assert {t for t, _ in dist} == {"integer", "text"}

# ---- 6. what we will NOT trust: a file can look like SQLite and be garbage ----------
bad = os.path.join(tmp, "notadb.db")
with open(bad, "wb") as f:
    f.write(b"just some text, not a database\n" * 10)
with open(bad, "rb") as f:
    assert f.read(16) != b"SQLite format 3\x00"
try:
    sqlite3.connect(bad).execute("SELECT count(*) FROM sqlite_master")
except sqlite3.DatabaseError as e:
    assert "file is not a database" in str(e)
    print("garbage file     :", e, " (a SQLCipher file says the same: check the header first)")
ro.close()

print("OK")
