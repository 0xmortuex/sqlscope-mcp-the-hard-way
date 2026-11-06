# SQLite MCP Server: The Hard Way

Build an MCP server that lets an AI agent explore an SQLite database file, and make it
**read-only by construction**: not "we only send SELECT", but a connection that cannot write
even if the agent tries every trick we could think of. Start from the 100-byte file header and
end with a server you have attacked on purpose.

At the end you will have built [sqlscope-mcp](https://github.com/0xmortuex/sqlscope-mcp)
yourself, in a few hundred lines, and you will understand the things most "SQL for your agent"
tutorials skip: **what SQLite will do if you let it**, and **how to turn a database into something
a model with a small context window can read**.

## Why This Exists

The usual demo hands the model `run_sql(query)` and a connection. SQLite looks harmless (it is
"just a file") until you test it:

- `mode=ro` stops `INSERT`, yet `VACUUM INTO 'x.db'` still **writes a new file** through it.
- `ATTACH` reaches other files, and `CREATE TABLE` inside the attached database works.
- Opening a **WAL-mode** database read-only quietly **creates `-wal` and `-shm` files** next to it.
- A text file, a DuckDB file and an encrypted SQLCipher file all answer `file is not a database`:
  three different problems, one useless message.
- `SELECT zeroblob(2000000000)` and an endless recursive CTE are "read-only" and still take a
  machine down.
- SQLite does **not enforce column types or foreign keys** by default, so "what is in this
  database?" has answers a schema listing cannot give.

Module 02 reproduces each of these on your machine before closing it.

## Who This Is For

- Developers who have built a "hello world" MCP server and want a real one
- Anyone who wants an agent to look inside an app's `.db` file without being able to change it
- Anyone curious how much SQLite will tell you about itself with nothing but `import sqlite3`

**Prerequisites:** Python and some SQL. No SQLite internals needed. Python 3.11 or newer.

```bash
pip install -r requirements.txt      # or: uv pip install -r requirements.txt
python verify.py
```

## Modules

| # | Module | What You'll Build |
|---|--------|-------------------|
| 01 | [File Format via Pragmas](01-file-format-via-pragmas/) | Read the 100-byte header by hand, match it to the PRAGMAs, see dynamic typing and free pages |
| 02 | [Read-Only Enforcement](02-read-only-enforcement/) | `mode=ro`, its three holes, `immutable=1`, `query_only`, an authorizer, a deadline, a size limit, and file-hash proof |
| 03 | [Introspection Queries](03-introspection-queries/) | Columns, keys, indexes, generated columns and CHECKs from pragma functions; a missing-FK-index finder; safe quoting |
| 04 | [Query Plans](04-query-plans/) | `EXPLAIN QUERY PLAN` as a tree, plain-language findings, and index advice as text |
| 05 | [Data-Quality Sampling](05-data-quality-sampling/) | Striding samples, `typeof()` distributions, orphan rows with foreign keys off, date-shape detection |
| 06 | [Output Shaping](06-output-shaping/) | Cells, rows and bytes with honest truncation; markdown, CSV, JSON; a timeout message the agent can act on |
| 07 | [MCP Wrapper and Testing](07-mcp-wrapper-and-testing/) | A three-tool server over real stdio, attacked by a real client, with hash-identical files afterwards |

Each module contains:
- **`README.md`**: the concepts and the design decisions
- **`main.py`**: a working, commented implementation of that stage. It asserts every claim the
  README makes and prints `OK` only if they all hold

## How To Use This Course

**Option A: Build along.** Read the modules in order, run each `main.py`, then do the
"Try breaking it" challenges at the end of each README.

**Option B: Verify.** `python verify.py` runs all seven modules (about 20 seconds; Module 07
launches a real MCP server and waits out a deliberate 10 s query timeout). `python verify.py 02 05`
runs just those.

Each `main.py` is self-contained, so Module 07 repeats the small pieces it needs from earlier
modules instead of importing them.

## What The Finished Server Has That This One Doesn't

The real [sqlscope-mcp](https://github.com/0xmortuex/sqlscope-mcp) adds five more tools
(`describe_table`, `schema_graph`, `find_in_db`, `data_quality`, `schema_docs`), a `--dir` folder
sandbox, FTS5 and virtual-table handling, a UTF-16 and invalid-UTF-8 pass, encrypted-file
detection, a hot-journal check and a 1,000,000-row test. Everything in those is built from the
pieces in Modules 03 to 06.

## License

MIT
