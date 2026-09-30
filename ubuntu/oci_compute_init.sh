#!/bin/sh
set -eu

repository_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec env OS_FAMILY=ubuntu "$repository_dir/oci_compute_init.sh" "$@"
