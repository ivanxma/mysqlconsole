#!/bin/sh
set -eu

repository_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec env OS_FAMILY=ol9 "$repository_dir/oci_compute_init.sh" "$@"
