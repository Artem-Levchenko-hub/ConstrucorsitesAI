# Skills index — fastapi-postgres

> Progressive-disclosure index (knowledge-layer plan §2). Only this index sits in
> the system prompt; read a full `<name>.md` with `read_file` when the task
> matches. Skills RAISE THE FLOOR; the deterministic gates (sast_gate over the
> Python files, accept_gauntlet) are the CEILING.

| Skill | When to read it (description) |
|---|---|
| `fastapi.md` | Before writing ANY endpoint, model, query, or migration. Production layering (routes→services→repositories), Pydantic validation at the edge, parameterized SQL, auth/authz, and Alembic migrations — secure + maintainable by construction. |

The web a11y/perf canons live with the front-end skeletons; this stack is the
API/compute tier. Security is enforced by the SAST gate (it scans `.py` for
eval/exec, `subprocess(shell=True)`, `os.system`, f-string/%-SQL, secrets) —
`fastapi.md` keeps you clear of all of them the first time.
