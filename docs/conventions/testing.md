# Validation and Testing Approach

Linked from [AGENTS.md](../../AGENTS.md).

Heavy dependencies (`cfgrib`, `psycopg2`, NOMADS HTTP) are not available in the
dev environment without a running stack. Follow this approach for any new or modified
file:

1. **`ast.parse` validation** — every generated Python file must parse cleanly.
   Include a shebang comment noting it was validated.
2. **Stub harness** — provide a lightweight smoke test using `unittest.mock` stubs
   for `psycopg2`, `cfgrib`, and HTTP calls so structure can be verified without
   the full stack.
3. **Stage incrementally** — deliver one logical unit at a time (e.g., one collector
   class), confirm it before proceeding to the next.
4. **Confirm assumptions before large implementations** — if the right approach is
   unclear, ask before writing a substantial block of code.
