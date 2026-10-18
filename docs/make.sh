#!/usr/bin/env bash
#
# Command file for Sphinx documentation (bash equivalent of make.bat).
#
# Usage:
#   ./make.sh          # list available builders
#   ./make.sh html     # build HTML into _build/html
#
# Environment:
#   SPHINXBUILD  path to sphinx-build (default: sphinx-build)
#   SPHINXOPTS   extra options passed to sphinx-build
#   O            shortcut for SPHINXOPTS, as in make.bat

set -e

cd "$(dirname "$0")"

SPHINXBUILD="${SPHINXBUILD:-sphinx-build}"
SOURCEDIR="."
BUILDDIR="_build"

if ! command -v "$SPHINXBUILD" >/dev/null 2>&1; then
    cat >&2 <<'EOF'
The 'sphinx-build' command was not found. Make sure you have Sphinx
installed, then set the SPHINXBUILD environment variable to point
to the full path of the 'sphinx-build' executable. Alternatively, you
may add the Sphinx directory to PATH.

If you don't have Sphinx installed, grab it from
https://www.sphinx-doc.org/
EOF
    exit 1
fi

if [ "$#" -eq 0 ]; then
    target="help"
else
    target="$1"
fi

# Word splitting is intentional: SPHINXOPTS/O may hold several options.
# shellcheck disable=SC2086
"$SPHINXBUILD" -M "$target" "$SOURCEDIR" "$BUILDDIR" ${SPHINXOPTS:-} ${O:-}
