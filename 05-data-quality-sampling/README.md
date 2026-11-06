# Module 05: Data-Quality Sampling

## The Concept

Because SQLite checks neither column types nor (by default) foreign keys, a schema tells you what
the data *should* look like and nothing about what it does. Data-quality checks ask the data. They
have to be cheap on a 10-million-row table, so they run over a **sample**, and every finding says
how big the sample was.

## Sampling That Is Not Biased

`SELECT ... LIMIT 5000` sees only the head of the table: usually the oldest rows, which are often
the cleanest. For a rowid table, stride instead:

```sql
SELECT * FROM users WHERE rowid % 40 = 0     -- every 40th row, across the whole table
```

The stride is `(max_rowid - min_rowid + 1) // sample`. SQLite still walks the b-tree, but it only has to look at the rowid of the rows it discards, which is
much cheaper than reading every column of every row. `main.py` asserts
the head sample ends at id 5000 while the strided one reaches past 190,000. Tables without a
usable rowid (WITHOUT ROWID, views) fall back to `LIMIT`, and the report says so.

## Four Checks

1. **Mixed storage types.** `SELECT typeof(col), count(*) FROM t GROUP BY 1`. An `INTEGER` column
   with `integer 199999, text 1` is a bug, and an example (`'n/a'` in row 8) makes it actionable.
   Integer + real in one column is common and mostly harmless; report it quietly.
2. **Duplicates after normalising.** Exact duplicates in an `email` column are visible to
   `GROUP BY email`. `USER5@example.com ` versus `user5@example.com` needs `GROUP BY lower(trim(email))`.
   Report both.
3. **Dangling foreign keys, with enforcement off.** `PRAGMA foreign_keys` is a per-connection
   setting that defaults to OFF, so a database written by an app that never enabled it can contain
   orphans. `NOT EXISTS (SELECT 1 FROM parent WHERE parent.id = child.fk)` finds them, using the
   parent's primary-key index. NULL foreign keys are legal and must be excluded.
4. **Date shape.** SQLite has no date type; dates are text. Replace every digit with `9` and count
   the shapes: `9999-99-99` x 199,995 and `99.99.9999` x 5 is a column someone imported from two
   sources. A column counts as date-like when more than 60 percent of its values match a date
   pattern.

## What `main.py` Does

Builds a 200,000-row `users` table with planted problems (text in an integer column, an email that
collides after `lower(trim())`, five Turkish-style dates) and a `posts` table with five orphans;
runs each check; asserts it finds exactly what was planted; and asserts the sampled checks finish
in under five seconds.

## Try Breaking It

- Make the sample size 50 and re-run. Which planted problems does the sample miss? A sample can
  prove a problem exists but never that it doesn't: how does the real server word its report?
- Plant a date like `2024-13-45`. The shape check passes it (`9999-99-99`); add a check that does not.
- Replace `rowid % stride` with `ORDER BY random() LIMIT n` and time it on the big table.
