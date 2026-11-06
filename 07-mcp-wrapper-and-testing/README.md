# Module 07: MCP Wrapper and Testing

## The Concept

The previous modules are a library. This one is the thin layer that makes it a server, and the
test that makes the claim "read-only by construction" something other than a sentence.

## The Wrapper

`MCPServer` turns a typed function with a docstring into a tool: the type hints become the input
schema, the docstring becomes the description the model reads when choosing a tool. Three tools:
`db_overview` (start here), `query` and `explain_query`. Three habits:

- **Descriptions are instructions.** "Start here." "Run ONE read-only SELECT." The model decides
  which tool to call from this text.
- **One failure type.** `ToolFailure` is raised for everything the agent can fix. The `@tool`
  decorator converts it to `ToolError`, because **MCPServer shows the client only a `ToolError`'s
  message**; any other exception arrives as a bare "Error executing tool". Genuine bugs stay
  unconverted on purpose, so they are not mistaken for advice.
- **Errors say what to do.** "Only SELECT / WITH / VALUES queries are allowed. This server is
  read-only by construction." beats `sqlite3.DatabaseError: not authorized`.

Each call opens a fresh connection and closes it: there is no state to leak, and no lock held between
calls.

## The Test

`drive()` is a real MCP client talking to the server **as a subprocess over stdio**, the way Claude
Desktop would. It:

1. creates a WAL-mode database (closed cleanly, so no `-wal`/`-shm`), an empty file and a text file;
2. takes a **hash of every file** in the folder;
3. lists the tools and calls each one;
4. sends ten hostile queries (`DROP`, stacked statements, `WITH ... DELETE`, `load_extension`,
   `VACUUM INTO`, an endless recursive CTE, `zeroblob(2000000000)`, a missing table) and checks that
   each comes back as `is_error` with a *specific* message;
5. opens three bad files and checks each message;
6. takes the hash again and asserts it is identical, with no file added: no `-wal`, `-shm` or
   `-journal`, no `copy.db`.

Step 6 is the whole point. A test that only checks the database still contains its rows would pass
while `VACUUM INTO` wrote a copy of your data into the working directory.

## What To Add Next

The real server adds the tools from Modules 03 to 05 (`describe_table`, `schema_graph`,
`find_in_db`, `data_quality`, `schema_docs`), a `--dir` sandbox that resolves `..` and symlinks
before checking a path, and tests with FTS5, a hot rollback journal, a UTF-16 database, a
1,000,000-row table and a path containing `#`, `%20` and Turkish letters.

## Try Breaking It

- Remove the `immutable` line from `open_readonly` and re-run `drive()`. Which assertion fails?
- Raise `RuntimeError` inside a tool. What does the client see? Now raise `ToolFailure`.
- Add a fourth tool that returns a table sample and make the 1,000,000-row case pass in
  under a second without `fetchall()`.
