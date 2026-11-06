# Module 03: Introspection Queries

## The Concept

An agent that understands a database starts from its schema, and SQLite can describe itself
without any parsing on your side: `sqlite_master` lists every object with its `CREATE` text, and
**pragma functions** return structured answers you can join and filter like tables.

| Question | Query |
|---|---|
| tables and views | `SELECT name, sql FROM sqlite_master WHERE type IN ('table','view')` |
| columns (incl. generated) | `SELECT * FROM pragma_table_xinfo(?)` |
| foreign keys | `SELECT * FROM pragma_foreign_key_list(?)` |
| indexes of a table | `SELECT * FROM pragma_index_list(?)` |
| columns of an index | `SELECT * FROM pragma_index_xinfo(?) WHERE key = 1` |
| row-count statistics | `sqlite_stat1` (exists only after `ANALYZE`) |

Pragma functions accept **bound parameters** (`pragma_table_xinfo(?)`), which is how you avoid
building SQL from a table name. Use `table_xinfo`, not `table_info`: only `xinfo` reports
generated columns (`hidden` is 2 for virtual and 3 for stored).

## Three Things That Need More Than a Pragma

1. **CHECK constraints** have no pragma. You parse the `CREATE TABLE` text: find `CHECK (`, then
   count parentheses to find the end. `main.py` does the simple version; the real server also skips
   string literals, quoted identifiers and comments, so `CHECK (b IN ('x)', 'y('))` works.
2. **Identifiers need quoting.** `"müşteriler"`, `select` and `we"ird` are all valid names. Every
   identifier that goes into SQL goes through `qi()`: wrap in double quotes, double any inner quote.
3. **Missing indexes on foreign keys.** A foreign key `orders.customer_id -> customers.id` is
   *covered* if some index (or the primary key) **starts with** exactly those columns, in any order.
   Without one, deleting a customer or joining customers to orders scans all of `orders`. The
   composite primary key `(order_id, sku)` covers `order_id` but **not** `sku`, which `main.py`
   asserts. The finder returns `CREATE INDEX` *text*; the server never runs it (it could not).

## Row Counts Without Scanning

`SELECT count(*) FROM big` reads the whole table. Prefer `sqlite_stat1` if it exists (its `stat`
column starts with the row count; it is an estimate from the last `ANALYZE`) and otherwise
`SELECT count(*) FROM (SELECT 1 FROM t LIMIT 100001)`, which stops early and lets you print
`>=100,000`.

## What `main.py` Does

Builds a database with a composite key, `WITHOUT ROWID`, a generated column, CHECK constraints,
cascading and plain foreign keys, an index, Turkish and quote-containing names; loads it into
small dataclasses; asserts every fact; and runs the missing-index finder.

## Try Breaking It

- Rename a table to contain a `'` and a space, and run `load()` again. Which line needs `qi()`?
- Add `UNIQUE (customer_id, placed)` to `orders`. Is `customer_id` now covered? Is `placed`?
- Write a CHECK that contains `)` inside a string literal and see `check_constraints` fail; then
  fix it by skipping quoted strings.
