#!/usr/bin/env bash
# Build (or rebuild) every orchestrator template image.
#
# Iterates `apps/orchestrator/templates/*/` and runs `docker build` for each
# template that has a `Dockerfile.dev`. Tag scheme: `omnia-template-<dir>:dev`.
# Same tag the provisioner looks for at run time.
#
# Idempotent: with --skip-cached, images already present are left alone.
# Without the flag, every template rebuilds (use after pulling new template
# code).
#
# Usage:
#   bash scripts/build-template-images.sh              # rebuild all
#   bash scripts/build-template-images.sh --skip-cached  # only build missing
#
# Exit 0 on success, non-zero if any build fails (other templates still
# attempted — the script reports a summary at the end).

set -uo pipefail

cd "$(dirname "$0")/.."

SKIP_CACHED=0
for arg in "$@"; do
    case "$arg" in
        --skip-cached) SKIP_CACHED=1 ;;
        -h|--help)
            sed -n '2,18p' "$0"
            exit 0
            ;;
        *)
            echo "unknown arg: $arg" >&2
            exit 2
            ;;
    esac
done

if [ ! -d templates ]; then
    echo "no templates/ directory in $(pwd)" >&2
    exit 1
fi

FAILED=()
BUILT=()
SKIPPED=()

for dir in templates/*/; do
    name=$(basename "$dir")
    dockerfile="$dir/Dockerfile.dev"
    if [ ! -f "$dockerfile" ]; then
        echo "[skip] $name — no Dockerfile.dev"
        continue
    fi

    tag="omnia-template-$name:dev"
    if [ "$SKIP_CACHED" -eq 1 ] && docker image inspect "$tag" >/dev/null 2>&1; then
        echo "[cached] $tag"
        SKIPPED+=("$name")
        continue
    fi

    echo "[build] $tag"
    if docker build -t "$tag" -f "$dockerfile" "$dir"; then
        BUILT+=("$name")
    else
        echo "[fail] $name" >&2
        FAILED+=("$name")
    fi
done

echo
echo "=== summary ==="
echo "built:   ${BUILT[*]:-(none)}"
echo "cached:  ${SKIPPED[*]:-(none)}"
echo "failed:  ${FAILED[*]:-(none)}"

[ ${#FAILED[@]} -eq 0 ]
