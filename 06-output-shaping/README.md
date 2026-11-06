# Module 06: Output Shaping

## The Concept

The reader of a tool result is a model with a finite context window, and everything you return
is spent from it. Output shaping is therefore a safety feature as much as a politeness. Four
independent caps, applied in this order:

1. **Cells.** A BLOB is never printed: show `<BLOB 1032 bytes 0x89504e47...>`, its size and 16
   bytes of hex, enough to recognise a PNG. Long text is cut and says how long it really was:
   `word word ... [10000 chars total]`. A truncation that hides the true length is a lie.
2. **Rows.** Never `fetchall()`. Ask the cursor for `max_rows + 1` rows: if you get the extra one,
   print `TRUNCATED`, and you never read the rest. `SELECT * FROM big` on a million rows takes
   about a millisecond. If everything fit, say nothing (a false "truncated" is noise).
3. **Bytes.** 200 rows of 200-character cells is still 40 KB. After the row cap, cut again by
   rendered size.
4. **Time.** A progress handler turns a runaway query into an error with an instruction in it:
   "Add a WHERE clause or LIMIT, or query an indexed column."

## Formats

One result, three renderings: markdown for reading, CSV for pasting into a spreadsheet, JSON for
the next tool in a pipeline. Every format uses the same cell function, so a BLOB looks the same
everywhere. Details that bite:

- Markdown cells must escape `|` and newlines or one value corrupts the table.
- JSON has no `inf`; `json.dumps` would emit invalid JSON, so non-finite floats become strings.
- Duplicate column names (`SELECT a.id, b.id`) collapse in a dict; the real server suffixes them.
- Do not truncate twice. Pre-formatting a cell and then passing it through a formatter with a
  smaller limit gives `... [62 chars total]` inside `... [5000 chars total]`, which is a bug the real server had in an early draft.

## What `main.py` Does

Builds 1000 documents with 10,000-character bodies and a million-row table; checks the cell
rules, the streaming row cap (and the absence of a false notice), the byte cap, agreement between
the three formats (including `inf`, quotes and a BLOB), and interrupts a three-way self-join of a
million rows after 0.3 s.

## Try Breaking It

- Replace `fetchmany(max_rows + 1)` with `fetchall()[:max_rows]` and time `SELECT * FROM big`.
- Return a BLOB as `.decode("utf-8", errors="replace")`. What does the model see, and what does it
  cost?
- Make `to_json` keep a 1 MB string whole. Where would it rather be cut: in the cell function, or
  in a final `clip()` on the whole response? Why both?
