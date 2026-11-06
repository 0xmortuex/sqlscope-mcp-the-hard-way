# Module 04: Query Plans

## The Concept

`EXPLAIN QUERY PLAN <select>` returns the strategy SQLite chose **without running the query**
(`main.py` proves it by explaining an endless recursive CTE instantly). Each row has an `id`, a
`parent` and a `detail` string, so the plan is a tree. For an agent, the tree is only half the
value: the other half is *what it means*.

| Plan text | Meaning | Advice |
|---|---|---|
| `SCAN orders` | every row is read | an index on the filtered column turns it into a SEARCH |
| `SCAN o USING COVERING INDEX i` | every index entry is read, table untouched | cheaper, still O(n) |
| `SEARCH orders USING INDEX i (placed=?)` | b-tree lookup | fine |
| `USE TEMP B-TREE FOR ORDER BY` / `GROUP BY` / `DISTINCT` | sorted in a temporary structure | an index in that order avoids it |
| `SEARCH o USING AUTOMATIC INDEX (customer_id=?)` | SQLite builds an index **on every run** because none exists | create the real one once |

Details worth knowing:

- **Aliases appear in the plan.** `FROM customers c` prints `SCAN c`. To say "full scan of
  customers" you must map the alias back; the real server reads `FROM`/`JOIN` clauses for that.
- Older SQLite prints `SCAN TABLE orders`; newer prints `SCAN orders`. Match both.
- A `SCAN` of a subquery or CTE is not a table scan; skip names you cannot resolve.
- Row counts make findings useful ("FULL TABLE SCAN of orders (5,000 rows)" versus 12 rows), but
  count with a cap so explaining a query never costs a table scan.

## Suggesting an Index (as text)

A server that is read-only cannot create indexes, and should not pretend to. It returns the
statement as text for the human to review:
`CREATE INDEX "idx_orders_customer_id" ON "orders" ("customer_id");`. The suggestion comes from either
an AUTOMATIC INDEX line (exact: SQLite names the columns) or, for plain scans, a heuristic over the
WHERE clause. Say "heuristic" when it is one.

## What `main.py` Does

Builds a 500 x 5000 row schema; renders plans as trees; asserts the findings for a full scan, an
indexed search, a sort with and without a usable index, and a join that triggers an automatic index.
It then applies the suggested index **on a separate writable connection** (the tutorial's own
setup, not the server) and asserts the plan changes to a SEARCH.

## Try Breaking It

- Explain `SELECT * FROM orders WHERE total > 100` after adding an index on `total`. SQLite may
  still scan. Why? (Look at selectivity and `ANALYZE`.)
- Alias both tables in the join and make `findings()` print the real table names.
- `EXPLAIN QUERY PLAN` a query with a typo. Where does the error surface, and is it a
  `DatabaseError` your wrapper turns into a message?
