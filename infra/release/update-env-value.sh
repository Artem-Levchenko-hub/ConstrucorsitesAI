#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 3 ]]; then
  echo "usage: update-env-value.sh FILE KEY VALUE" >&2
  exit 2
fi

input_file="$1"
key="$2"
value="$3"

if [[ ! "${key}" =~ ^[A-Z][A-Z0-9_]*$ ]]; then
  echo "invalid environment key" >&2
  exit 2
fi
if [[ "${value}" == *$'\n'* || "${value}" == *$'\r'* ]]; then
  echo "environment value must be one line" >&2
  exit 2
fi
if [[ ! -f "${input_file}" || -L "${input_file}" ]]; then
  echo "environment file must be an existing regular file" >&2
  exit 2
fi

file_dir="$(cd "$(dirname "${input_file}")" && pwd -P)"
file_path="${file_dir}/$(basename "${input_file}")"
temp_file="$(mktemp "${file_dir}/.$(basename "${file_path}").tmp.XXXXXX")"
trap 'rm -f "${temp_file}"' EXIT

if stat -f '%Lp' "${file_path}" >/dev/null 2>&1; then
  file_mode="$(stat -f '%Lp' "${file_path}")"
  file_uid="$(stat -f '%u' "${file_path}")"
  file_gid="$(stat -f '%g' "${file_path}")"
else
  file_mode="$(stat -c '%a' "${file_path}")"
  file_uid="$(stat -c '%u' "${file_path}")"
  file_gid="$(stat -c '%g' "${file_path}")"
fi

while IFS= read -r line || [[ -n "${line}" ]]; do
  if [[ "${line}" == "${key}="* ]]; then
    continue
  fi
  printf '%s\n' "${line}" >>"${temp_file}"
done <"${file_path}"
printf '%s=%s\n' "${key}" "${value}" >>"${temp_file}"

chmod "${file_mode}" "${temp_file}"
chown "${file_uid}:${file_gid}" "${temp_file}" 2>/dev/null || true
mv -f "${temp_file}" "${file_path}"
trap - EXIT

echo "updated ${key} in ${input_file}"
