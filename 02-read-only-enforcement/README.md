# Module 02: Read-Only Enforcement

## The Concept

"Read-only" is a claim about **every possible statement**, not about the ones you thought of.
The tempting gate is a string check (`sql.startswith("SELECT")`); it is easy to fool, and it does
nothing about a read-only query that still hurts. So we build layers, and we **test each one by
attacking it**, because the first thing that test found in this module was that the obvious layer
has holes.

## Layer 1: the URI

```python
sqlite3.connect(Path(p).as_uri() + "?mode=ro", uri=True)
```

stops `INSERT`, `UPDATE`, `DROP`, `CREATE`, `PRAGMA user_version = 5`. Good. And then `main.py`
shows what it does not stop:

| Hole | What happens |
|------|--------------|
| `VACUUM INTO 'file'` | writes a **new file** from a `mode=ro` connection |
| `ATTACH ':memory:'` | succeeds; `CREATE TABLE` in the attached database succeeds too. `ATTACH 'other.db'` reaches other files |
| WAL-mode database | opening it creates `name.db-wal` and `name.db-shm` beside it |

The third matters most for the promise "I did not touch your files". A WAL database needs a shared
memory index; a read-only connection still creates it.

## Layer 2: `immutable=1` when it is safe

`?mode=ro&immutable=1` tells SQLite the file cannot change, so it takes no locks and creates no side
files. But it also **ignores the WAL**, so if a writer has uncommitted-to-the-main-file changes sitting in
`name.db-wal` you would read stale data. The rule `main.py` demonstrates:

- header says WAL **and** there is no non-empty `-wal` file: nothing is pending, use `immutable=1`
- a non-empty `-wal` exists: use plain `mode=ro` (it reads the WAL, and leaves the database and
  the WAL file byte-identical; only SQLite's own `-shm` index may be touched)

## Layer 3: `query_only` and the authorizer

`PRAGMA query_only = ON` is a second, independent write stop. The strong layer is the
**authorizer**: SQLite calls your function for every action while *compiling* a statement, with an
action code and two names. You return `SQLITE_OK` or `SQLITE_DENY`. The right shape is an
**allow-list**:

- allow `SELECT`, `READ` (a column read), `FUNCTION` (a call), `RECURSIVE` (recursive CTEs)
- allow a handful of **read-form** pragmas that introspection needs (`table_info`, `index_list`, ...)
- deny everything else: `INSERT`, `ATTACH`, `VACUUM`, `CREATE TEMP TABLE`, `PRAGMA x = y`, `BEGIN`
- deny two functions by name: `load_extension`, `fts3_tokenizer`

Because it works on compiled statements, `WITH x AS (SELECT 1) DELETE FROM t` is caught as well,
which a first-word check would let through. Install it **after** setting `query_only` (the authorizer would deny that
pragma). Note that table-valued pragma functions (`SELECT * FROM pragma_table_info('t')`) are
checked too, so the allow-list works for them.

The error text differs by action (`not authorized`, `authorization denied`, `attempt to write a
readonly database`), so match on all of them when you turn it into a message for the agent.

## Layers 4 and 5: time and size

A read-only query can still run forever (`WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n+1
FROM r) SELECT ...`) or build a 2 GB value (`zeroblob(2000000000)`). A **progress handler**
(called every N virtual-machine instructions; return non-zero to abort) enforces a deadline, and
`setlimit(SQLITE_LIMIT_LENGTH, 64 MiB)` makes oversize values fail instead of allocating.

## The Proof

`snapshot(folder)` hashes every file in the folder. The test runs 19 attacks on a guarded
connection, then asserts `snapshot(after) == snapshot(before)`. That one comparison also catches
a file *created* by an attack (the `VACUUM INTO` case), which a check of the database alone would
miss. The test even changes directory into the folder first, so a stray relative-path write lands
where the snapshot can see it.

## Try Breaking It

- Add `"integrity_check"` to `SAFE_PRAGMAS` and run it against a large file. It only reads, so is it
  "safe"? What stops it from running for an hour? (The deadline, which is why layer 4 exists.)
- Install the authorizer *before* `PRAGMA query_only = ON`. What error do you get, and why?
- Delete the `immutable` branch and open the WAL database with plain `mode=ro`. Which assertion
  fails, and what is left in the folder?
- Write an attack that is not in `ATTACKS`, run it, and decide whether it needs a deny-list entry.
  (The real server's attack suite has 53 statements; it started with about 20.)
