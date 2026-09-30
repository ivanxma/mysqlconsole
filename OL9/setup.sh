#!/bin/sh
set -eu

repository_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec "$repository_dir/setup.sh" ol9 "$@"
