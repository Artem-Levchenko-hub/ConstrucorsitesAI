# Domain Intelligence — Fitness

This is not a template. Model fitness as a personal progression system, not a
generic metrics dashboard.

## Domain objects and loop

Useful objects include programme, workout, exercise, set, effort, personal
record, recovery signal and coach recommendation. The core loop is usually:
prepare → perform/log → immediate feedback → review trend → adjust next session.
Choose only the objects the brief needs.

- Make today's action and readiness visible before historical analytics.
- Logging a set must be fast, thumb-friendly and resilient to interruption.
- Separate planned, completed, skipped and partial work; never fabricate
  completion or silently replace user values.
- Explain trends with baseline, period and units. Do not treat weight, duration
  and effort as interchangeable scores.
- AI coaching must cite the user's available context, express uncertainty and
  avoid diagnosis or unsafe certainty.

## States and trust

Use a genuinely empty first-run state with a clear first workout action. Saved
workouts come from authenticated persistence, never canned history. Handle an
unfinished workout, offline/retry, deleted exercise, no comparable period and
missing wearable data. Health or medical claims trigger `trust-safety`.

## Activation and retention

Activation is a completed first meaningful log or plan, not viewing statistics.
Support return with continuity: next session, recovery check or progress insight.
Avoid guilt mechanics and streak punishment; reward evidence of progress.
