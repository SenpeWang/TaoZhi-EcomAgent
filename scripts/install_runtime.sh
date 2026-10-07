#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME="$HOME/.local/share/ecom-v3/runtime"
mkdir -p "$RUNTIME"
BUILD_DIR="$(mktemp -d "$RUNTIME/.build-XXXXXX")"
trap 'rm -rf "$BUILD_DIR"' EXIT
cd "$BUILD_DIR"
if [ ! -x "$RUNTIME/node/bin/node" ]; then
 curl -fL --retry 2 -o node.tar.xz https://nodejs.org/dist/v24.21.0/node-v24.21.0-linux-x64.tar.xz
 printf '%s  %s\n' fd8e59d5a511510f6a298afb548f18c7d2b1be404d8b4a27d94fbe49f56cb2d6 node.tar.xz | sha256sum -c -
 tar -xf node.tar.xz
 mv node-v24.21.0-linux-x64 "$RUNTIME/node"
fi
if [ ! -x "$RUNTIME/postgres/bin/postgres" ]; then
 mkdir -p "$BUILD_DIR/packages" "$BUILD_DIR/tools"
 cd "$BUILD_DIR/packages"
 apt-get download bison flex
 for pkg in ./*.deb; do dpkg-deb -x "$pkg" "$BUILD_DIR/tools"; done
 export PATH="$BUILD_DIR/tools/usr/bin:$PATH"
 export BISON_PKGDATADIR="$BUILD_DIR/tools/usr/share/bison"
 cd "$BUILD_DIR"
 curl -fL --retry 2 -o postgres.tar.bz2 https://ftp.postgresql.org/pub/source/v18.6/postgresql-18.6.tar.bz2
 printf '%s  %s\n' 555610c24d53e4316da5b7d3fc25c279d96856d5e0e23ee308c328c5fa881d9f postgres.tar.bz2 | sha256sum -c -
 tar -xf postgres.tar.bz2
 cd postgresql-18.6
 ./configure --prefix="$RUNTIME/postgres" --without-readline --without-icu
 make -j4
 make install
fi
cd "$ROOT"
if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
 python3 -m pip install --target "$BUILD_DIR/python_tools" virtualenv
 PYTHONPATH="$BUILD_DIR/python_tools" python3 -m virtualenv .venv
fi
.venv/bin/python -m pip install -r requirements.lock.txt
printf '%s\n' RUNTIME_READY
