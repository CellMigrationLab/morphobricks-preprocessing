#!/usr/bin/env bash
# Usage: ZARR529_DATA=<dir with the stores> ZARR529_OUT=<output dir> [ZARR529_PYTHON=<python>] run_ebi.sh
# ZARR529_DATA holds the stores at the relative paths named below; ZARR529_OUT receives the JSON records.
set -u
: "${ZARR529_DATA:?set ZARR529_DATA to the directory that holds the downloaded stores}"
: "${ZARR529_OUT:?set ZARR529_OUT to the directory for the JSON records}"
[ -d "$ZARR529_DATA" ] || { echo "ZARR529_DATA is not a directory: $ZARR529_DATA" >&2; exit 2; }
mkdir -p "$ZARR529_OUT" || exit 2
ZARR529_DATA="$(cd "$ZARR529_DATA" && pwd)"
ZARR529_OUT="$(cd "$ZARR529_OUT" && pwd)"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ZARR529_OUT" || exit 2
P="${ZARR529_PYTHON:-python3} -I $HERE/lazy529.py"
F=$ZARR529_DATA/ebi_cy5/cy5.ome.zarr
A='"axes":"TCZYX"'
uptime
$P windows "{\"path\":\"$F\",$A,\"tile\":1024,\"margin\":64,\"channels\":[0],\"t\":0,\"z\":0,\"workers\":1,\"cache\":\"plan\"}" ebi_win_w1_plan.json
$P windows "{\"path\":\"$F\",$A,\"tile\":1024,\"margin\":64,\"channels\":[0],\"t\":0,\"z\":0,\"workers\":4,\"cache\":\"plan\"}" ebi_win_w4_plan.json
$P windows "{\"path\":\"$F\",$A,\"tile\":1024,\"margin\":64,\"channels\":[0],\"t\":0,\"z\":0,\"workers\":4,\"cache\":1}" ebi_win_w4_nocache.json
$P xyc "{\"path\":\"$F\",$A,\"planes\":[[0,0]]}" ebi_xyc.json
$P planes "{\"path\":\"$F\",$A,\"memory_limit\":67108864}" ebi_planes_64m.json
$P planes "{\"path\":\"$F\",$A,\"memory_limit\":1073741824}" ebi_planes_1g.json
uptime
$P verify "{\"path\":\"$F\",$A,\"records\":[\"ebi_win_w1_plan.json\",\"ebi_win_w4_plan.json\",\"ebi_win_w4_nocache.json\",\"ebi_xyc.json\",\"ebi_planes_64m.json\",\"ebi_planes_1g.json\"]}" ebi_verify.json
