# MAX platform-native product lens

- Treat MAX identity as existing product context. Use the managed profile/client
  and signed session; do not add separate email login or ask again for data MAX
  already supplies.
- Use the installed MAX Bridge and Omnia integration client. Read the locked files
  or call `docs` when an API signature is uncertain; never invent a bridge method,
  package export or server route.
- Keep platform chrome and system gestures in mind: mobile safe areas, keyboard,
  back/dismiss behaviour, sheets and bottom navigation must not fight the host.
- Haptics reinforce high-value moments—selection, completion, warning—not every
  tap. Always retain visual feedback when haptics are unavailable.
- Deep links, sharing, notifications or bot actions should exist only when the
  requested flow needs them and a real managed primitive supports them.
- Network and bridge availability are runtime conditions. Provide a recoverable
  state rather than hiding failure behind optimistic success.
- Keep secrets and privileged operations server-side through managed actions.
  Product files must not access the database directly or recreate `/api/max` and
  `/api/omnia` routes.

Native means fewer redundant steps and coherent host behaviour—not copying a
system component onto every surface.
