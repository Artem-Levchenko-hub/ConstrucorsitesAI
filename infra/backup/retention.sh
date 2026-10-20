#!/usr/bin/env bash
# Shared, fail-closed policy for local and object-store backup retention.

backup_retention_validate() {
  case "$1" in true|false) ;; *)
    echo 'BACKUP_PRUNE_ENABLED must be true or false' >&2; return 64 ;;
  esac
  [[ "$2" =~ ^[1-9][0-9]*$ ]] || {
    echo 'RETENTION_DAYS must be a positive integer' >&2; return 64;
  }
}

backup_prune_local() {
  local root="$1" days="$2" enabled="$3" physical_root
  backup_retention_validate "$enabled" "$days" || return
  [[ "$enabled" == true ]] || return 0
  [[ -d "$root" && ! -L "$root" ]] || {
    echo 'Backup retention root must be an existing regular directory' >&2; return 65;
  }
  physical_root="$(cd -- "$root" && pwd -P)" || return
  [[ "$physical_root" != / ]] || {
    echo 'Refusing retention against filesystem root' >&2; return 65;
  }
  # Resolve the parent once; find does not follow symlinks, and only timestamped
  # direct children may be deletion targets. Preservation directories stay put.
  find "$physical_root" -regextype posix-extended -mindepth 1 -maxdepth 1 \
    -type d -regex '.*/20[0-9]{6}-[0-9]{6}' -mtime "+$days" -exec rm -rf -- {} +
}

backup_prune_minio() {
  local bucket="$1" days="$2" enabled="$3" cutoff listing prefix
  backup_retention_validate "$enabled" "$days" || return
  [[ "$enabled" == true ]] || return 0
  [[ "$bucket" =~ ^[a-z0-9][a-z0-9.-]+[a-z0-9]$ ]] || {
    echo 'Invalid backup bucket name' >&2; return 65;
  }
  cutoff="$(date -u -d "-$days days" +%Y%m%d-%H%M%S)" || return
  listing="$(minio_mc ls "l/$bucket/" 2>/dev/null)" || {
    echo 'Unable to list backup prefixes for retention' >&2; return 1;
  }
  while IFS= read -r prefix; do
    prefix="${prefix%/}"
    [[ "$prefix" =~ ^20[0-9]{6}-[0-9]{6}$ && "$prefix" < "$cutoff" ]] || continue
    minio_mc rm --recursive --force "l/$bucket/$prefix/" >/dev/null 2>&1 || {
      echo 'Unable to prune expired backup prefix' >&2; return 1;
    }
  done < <(printf '%s\n' "$listing" | awk '{print $NF}')
}
