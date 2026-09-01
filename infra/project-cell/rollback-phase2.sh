#!/usr/bin/env bash
set -euo pipefail

printf '%s\n' \
  'phase2 rollback failed: host rollback mutation is hard-disabled in this delivery' >&2
exit 1
