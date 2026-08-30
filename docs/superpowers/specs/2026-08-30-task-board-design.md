# Team Task Board Design

## Objective

Add a small shared task board at `/doska` for four fixed team members: Алексей, Алексей jr., Артем, and Роман. The board must let the team create, edit, assign, delete, and move tasks across workflow states while keeping the data shared between browsers.

## Scope

- Public route: `/doska`.
- Fixed participant profiles: `alexey`, `alexey_jr`, `artem`, and `roman`.
- Workflow columns: `backlog`, `in_progress`, `review`, and `done`.
- Task fields: title, description, assignee, priority, status, position, and timestamps.
- Identity switcher in the header. The selected identity is stored only as a convenience in the browser; it preselects the assignee for new tasks and is not an authentication boundary.
- Shared persistence in the existing PostgreSQL database through the Omnia API.
- Native HTML drag-and-drop on pointer devices, plus a status field in the edit form so the same workflow remains usable on touch devices.
- Responsive dark Omnia interface with horizontally independent columns on large screens and stacked columns on small screens.

## Architecture

The API owns validation and persistence. A `task_board_tasks` table stores one row per card, and a small public CRUD router under `/api/task-board/tasks` exposes the board. The fixed members and statuses are expressed as identical literals in the Pydantic and TypeScript contracts. A status change appends the card to the destination column by assigning the next position in that column. A transaction-scoped PostgreSQL advisory lock serializes position allocation across browsers and API workers.

The Next.js page is intentionally outside the authenticated `(app)` route group so the four-person team can open the supplied URL directly. `TaskBoard` loads tasks on mount, refreshes the list periodically, and applies create, update, move, and delete operations through the typed API adapter. Request and mutation sequence guards prevent a slow poll response from undoing a newer local action, while each card is temporarily locked during its own mutation so responses cannot finish out of order. Errors stay visible in the board header without discarding the last successfully loaded state.

## UX

- Header: product name, compact member switcher, and primary “Новая задача” action.
- Summary strip: total tasks and counts for active/review/done work.
- Board: four labeled columns with count badges and clear empty states.
- Card: priority marker, title, optional description, assignee avatar/name, and edit/delete actions.
- Create/edit sheet: title, description, assignee, priority, and status when editing.
- Drag feedback: highlighted destination column and reduced-opacity source card.
- Empty, loading, saving, and request-error states are explicit.

## Data and validation

- Titles are trimmed and limited to 160 characters.
- Descriptions are limited to 2,000 characters.
- Only the four member ids, four statuses, and three priorities are accepted.
- Deleting a missing card returns 404; updating a missing card returns 404.
- Every status move is committed before the refreshed card is returned.
- Public mutations are limited to 30 per minute per client IP and the board is capped at 500 cards.
- Position allocation is serialized so concurrent creates and moves receive stable, unique positions.
- The migration creates the table, status/assignee/priority check constraints, a status-position index, and the standard `updated_at` trigger.

## Verification

- API integration tests cover an empty board, creation, validation, update/move, deletion, concurrent position allocation, and capacity protection.
- Frontend component tests cover the four accounts, the four columns, creating under the selected member, moving a card to another status, stale-poll protection, and per-card mutation serialization.
- Repository checks: targeted Pytest, Vitest, API Ruff, frontend lint/typecheck, production build, Alembic single-head test, and diff/report sanity checks.
- Production checks: both changed services rebuild, API migration reaches head, `/api/task-board/tasks` returns 200, `/doska` returns 200, the release SHA matches the pushed commit, and the board is visually inspected in the browser.

## Non-goals

- Passwords, invitations, permissions, notifications, comments, attachments, due dates, and activity history.
- Arbitrary team-member management or custom workflow columns.
- Real-time sockets; periodic refresh is sufficient for this small internal board.
