#!/usr/bin/env bash
set -euo pipefail

source /etc/os-release
suite="${VERSION_CODENAME:?Ubuntu codename is missing}"

release_available() {
  curl --fail --silent --location --head --output /dev/null \
    --connect-timeout 4 --max-time 8 "$1/dists/$2/InRelease"
}

if release_available https://archive.ubuntu.com/ubuntu "$suite" &&
   release_available https://security.ubuntu.com/ubuntu "$suite-security"; then
  exit 0
fi

sources=/etc/apt/sources.list.d/ubuntu.sources
grep -q '^Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg$' "$sources"
for mirror in https://mirrors.edge.kernel.org/ubuntu https://mirror.math.princeton.edu/pub/ubuntu; do
  if release_available "$mirror" "$suite" && release_available "$mirror" "$suite-security"; then
    # Change the endpoint while retaining Ubuntu's existing suites and archive keyring.
    sudo sed -i "s|^URIs: .*|URIs: $mirror/|" "$sources"
    printf 'Using Ubuntu archive mirror %s for %s\n' "$mirror" "$suite"
    exit 0
  fi
done

printf 'Ubuntu package archives are unreachable for %s\n' "$suite" >&2
exit 1
