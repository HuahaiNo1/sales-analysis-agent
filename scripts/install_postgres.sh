#!/usr/bin/env bash
# Unprivileged development-only PostgreSQL installation for Debian 13 x86_64.
# Downloads official Debian packages and extracts files locally. No sudo, package
# manager state changes, service start, database initialization or port changes.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${POSTGRES_INSTALL_ROOT:-$ROOT/runtime/postgres}"
CACHE="${POSTGRES_PACKAGE_CACHE:-$ROOT/runtime/pg-packages}"
PACKAGE_VERSION='17.11-0+deb13u1'
BASE='https://ftp.debian.org/debian/pool/main/p/postgresql-17'
for tool in curl dpkg-deb sha256sum; do
  command -v "$tool" >/dev/null || { echo "Required tool not found: $tool" >&2; exit 1; }
done
if [[ "$(uname -s)" != Linux || "$(uname -m)" != x86_64 ]]; then
  echo 'This local installer supports Debian 13 x86_64. Use an official PostgreSQL 17 installation on other platforms.' >&2
  exit 1
fi
# These binaries are built for Debian 13; do not silently use incompatible system libraries.
source /etc/os-release
if [[ "${ID:-}" != debian || "${VERSION_ID:-}" != 13 ]]; then
  echo 'This installer is pinned to Debian 13. Use your official PostgreSQL 17 package on another system.' >&2
  exit 1
fi
BIN="$DEST/usr/lib/postgresql/17/bin"
export LD_LIBRARY_PATH="$DEST/usr/lib/x86_64-linux-gnu:${LD_LIBRARY_PATH:-}"
if [[ -x "$BIN/postgres" && -x "$BIN/initdb" && -x "$BIN/psql" ]]; then
  "$BIN/postgres" --version
  "$BIN/psql" --version
  echo "Existing local PostgreSQL kept at $DEST"
  exit 0
fi
mkdir -p "$DEST" "$CACHE"
packages=(postgresql-17 postgresql-client-17 libpq5)
checksums=(
  d2ce1ddffafa783f9acda4c92c86fc21e8288bff3782d739294f14fa797d7886
  9d8558f8dd57c8e92e218a20698383575d53742ca3f9e7c2b7fe5f246d5216ae
  20a4c9ef58b4baf90deda67cfb2cc062871c062830dddc234d28f5ac7931b86b
)
for i in "${!packages[@]}"; do
  package="${packages[$i]}"
  file="$CACHE/$package.deb"
  if [[ ! -f "$file" ]]; then
    curl --fail --location --silent --show-error --retry 2 \
      "$BASE/${package}_${PACKAGE_VERSION}_amd64.deb" -o "$file.partial"
    mv "$file.partial" "$file"
  fi
  printf '%s  %s\n' "${checksums[$i]}" "$file" | sha256sum --check -
  [[ "$(dpkg-deb --field "$file" Version)" == "$PACKAGE_VERSION" ]]
  [[ "$(dpkg-deb --field "$file" Architecture)" == amd64 ]]
  dpkg-deb --extract "$file" "$DEST"
done
"$BIN/postgres" --version
"$BIN/initdb" --version
"$BIN/psql" --version
printf 'PostgreSQL extracted to %s\nRun bash scripts/start_postgres.sh to initialize the development database.\n' "$DEST"
