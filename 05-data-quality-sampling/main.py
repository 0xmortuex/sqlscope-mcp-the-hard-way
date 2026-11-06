"""Module 05: data-quality checks that stay cheap on big tables: sampling, typeof(), orphans, date shapes.

Run: python 05-data-quality-sampling/main.py
"""

from __future__ import annotations

import os
import pathlib
import re
import sqlite3
import tempfile
import time
from collections import Counter

tmp = tempfile.mkdtemp()
path = os.path.join(tmp, "quality.db")
w = sqlite3.connect(path)
w.executescript(
    """
    CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, signup TEXT, score INTEGER);
    CREATE TABLE posts (id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id), body TEXT);
    """
)
rows = []
for i in range(1, 200_001):
    signup = f"2024-02-{(i % 28) + 1:02d}"
    if i % 40_000 == 0:
        signup = "15.02.2024"  # a Turkish-style date among ISO dates
    score = i % 100
    rows.append((i, f"user{i}@example.com", signup, score))
rows[7] = (8, "user8@example.com", "2024-02-09", "n/a")  # text in an INTEGER column
rows[99] = (100, "USER5@example.com ", "2024-02-01", 5)  # collides with user5 only after lower() and trim()
rows[4] = (5, "user5@example.com", "2024-02-06", 5)
w.executemany("INSERT INTO users VALUES (?, ?, ?, ?)", rows)
w.executemany("INSERT INTO posts VALUES (?, ?, ?)", [(i, (i % 200_000) + 1, "hi") for i in range(1, 50_001)])
w.executemany("INSERT INTO posts VALUES (?, ?, ?)", [(60_000 + i, 999_000 + i, "orphan") for i in range(5)])  # parents do not exist
w.commit()
w.close()

con = sqlite3.connect(pathlib.Path(path).as_uri() + "?mode=ro", uri=True)


def sample_from(con: sqlite3.Connection, table: str, sample: int) -> tuple[str, str]:
    """A FROM fragment yielding about `sample` rows spread over the whole table (not just the first ones)."""
    n = con.execute(f'SELECT count(*) FROM (SELECT 1 FROM "{table}" LIMIT ?)', (sample + 1,)).fetchone()[0]
    if n <= sample:
        return f'"{table}"', "all rows"
    lo, hi = con.execute(f'SELECT min(rowid), max(rowid) FROM "{table}"').fetchone()
    stride = max(2, (hi - lo + 1) // sample)
    return f'(SELECT * FROM "{table}" WHERE rowid % {stride} = 0)', f"every {stride}th rowid"


# 1. sampling: ~5,000 rows out of 200,000, spread evenly
src, how = sample_from(con, "users", 5000)
n = con.execute(f"SELECT count(*) FROM {src}").fetchone()[0]
assert 4000 <= n <= 6000, n
print(f"sample: {n} rows ({how})")
first = con.execute("SELECT max(id) FROM (SELECT id FROM users LIMIT 5000)").fetchone()[0]
spread = con.execute(f"SELECT max(id) FROM {src}").fetchone()[0]
assert first == 5000 and spread > 190_000, "LIMIT only sees the head of the table; striding sees all of it"

# 2. typeof(): SQLite lets an INTEGER column hold text. Report the distribution, with examples.
dist = con.execute("SELECT typeof(score), count(*) FROM users GROUP BY 1 ORDER BY 2 DESC").fetchall()
assert dist == [("integer", 199_999), ("text", 1)], dist
ex = con.execute("SELECT id, score FROM users WHERE typeof(score) = 'text' LIMIT 3").fetchall()
assert ex == [(8, "n/a")]
print("typeof(score):", dist, "example:", ex)

# 3. duplicates that only exist after normalising: lower() + trim()
exact = con.execute("SELECT count(*) FROM (SELECT email FROM users GROUP BY email HAVING count(*) > 1)").fetchone()[0]
norm = con.execute("SELECT count(*) FROM (SELECT lower(trim(email)) e FROM users GROUP BY e HAVING count(*) > 1)").fetchone()[0]
assert exact == 0 and norm == 1, (exact, norm)
print("duplicate emails: exact =", exact, "after lower/trim =", norm)

# 4. orphans without trusting PRAGMA foreign_keys (off by default!). NOT EXISTS uses the parent's index.
assert con.execute("PRAGMA foreign_keys").fetchone() == (0,), "SQLite does not enforce FKs unless asked, per connection"
orphans = con.execute(
    "SELECT count(*) FROM posts c WHERE c.user_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM users p WHERE p.id = c.user_id)"
).fetchone()[0]
assert orphans == 5
print("orphan posts:", orphans)

# 5. date-looking text: classify values by SHAPE (digits -> 9) and count the shapes
DATE_LIKE = re.compile(r"^\s*(\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})")
vals = [v for (v,) in con.execute("SELECT signup FROM users WHERE typeof(signup) = 'text'")]
assert sum(1 for v in vals if DATE_LIKE.match(v)) / len(vals) > 0.9, "date-looking column"
shapes = Counter(re.sub(r"\d", "9", v) for v in vals)
assert shapes == Counter({"9999-99-99": 199_995, "99.99.9999": 5}), shapes
print("date shapes:", dict(shapes))

# 6. the whole sampled report on a 200k-row table is fast
t0 = time.monotonic()
for col in ("email", "signup", "score"):
    con.execute(f"SELECT typeof({col}), count(*) FROM {src} GROUP BY 1").fetchall()
    con.execute(f"SELECT sum({col} IS NULL), count(DISTINCT {col}) FROM {src}").fetchall()
assert time.monotonic() - t0 < 5
print("OK")
