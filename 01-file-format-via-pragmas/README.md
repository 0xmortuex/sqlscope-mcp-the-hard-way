# Module 01: File Format via Pragmas

## The Concept

An SQLite database is **one file made of equal-sized pages**. The first 100 bytes are a header
that says how big a page is, how many pages there are, which text encoding is used, whether the
file uses a rollback journal or a write-ahead log, and two integers applications may use
(`user_version`, `application_id`). Everything an overview tool wants to say about a file is
either in that header or one `PRAGMA` away.

`main.py` builds a small database, then reads the header **twice**: with `struct.unpack` straight
from the bytes, and with PRAGMAs, and asserts they agree.

| Header bytes | Meaning | Pragma |
|---|---|---|
| 0-15 | `SQLite format 3\0` magic | none (check it yourself) |
| 16-17 | page size (1 means 65536) | `page_size` |
| 18-19 | 1 = rollback journal, 2 = WAL | `journal_mode` |
| 28-31 | number of pages | `page_count` |
| 36-39 | free (reusable) pages | `freelist_count` |
| 56-59 | text encoding (1 = UTF-8) | `encoding` |
| 60-63 / 68-71 | `user_version` / `application_id` | same names |

Two consequences matter to a server:

1. **File size = page size x page count.** A table you just emptied does not shrink the file; the
   pages go on a free list. An overview that reports "80 KiB" and "3 rows" is not wrong, so show
   `freelist_count` and let the agent understand.
2. **The header is the cheapest, safest test.** `sqlite3.connect()` happily opens *any* file and
   fails later with `file is not a database`. That message is what a text file, a DuckDB file,
   a truncated download and an encrypted SQLCipher database all produce. Read 100 bytes first and
   you can say which problem it is (empty, wrong format, header present but damaged, or "the first
   16 bytes look random": encrypted).

## Dynamic Typing

SQLite lets an `INTEGER` column hold the text `'not a number'`. The declared type only sets an
*affinity*: `'42'` is converted to the integer 42, `'n/a'` is stored as text. The only honest way to
know what a column holds is `typeof(col)`. Module 05 turns this into a check.

## What `main.py` Does

1. Builds a database (`user_version`, `application_id`, a table, an index, 2000 rows, then deletes half).
2. Reads the header bytes and the PRAGMAs and asserts they match.
3. Asserts `file size == page_size * page_count` and that the delete left free pages.
4. Lists `sqlite_master`, SQLite's own catalog, which is a plain table.
5. Inserts text into an INTEGER column and shows `typeof()` splitting the column.
6. Opens a text file with `sqlite3` to capture the unhelpful error message.

## Try Breaking It

- Open the file in a hex editor and change byte 18 to 2. What does `PRAGMA journal_mode` say? Does
  the connection still open? Predict first, then look at the folder afterwards (Module 02 cares about that).
- Make `check_header()` that distinguishes the four bad-file cases above using only the first 100
  bytes. The real server's `check_file` is about 40 lines.
- Why does `page_size_raw == 1` mean 65536? (A 16-bit field cannot hold 65536.)
