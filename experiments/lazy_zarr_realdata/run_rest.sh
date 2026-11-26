#!/usr/bin/env bash
# Usage: ZARR529_DATA=<dir with the stores> ZARR529_OUT=<output dir> [ZARR529_PYTHON=<python>] run_rest.sh
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
F=$ZARR529_DATA/ssbd_rgb/brainslice.ome.zarr; A='"axes":"TCZYX"'
uptime
$P windows "{\"path\":\"$F\",$A,\"tile\":2048,\"margin\":128,\"channels\":[0,1,2],\"t\":0,\"z\":0,\"workers\":1,\"cache\":\"plan\"}" rgb_win_w1_plan.json
$P windows "{\"path\":\"$F\",$A,\"tile\":2048,\"margin\":128,\"channels\":[0,1,2],\"t\":0,\"z\":0,\"workers\":4,\"cache\":\"plan\"}" rgb_win_w4_plan.json
$P windows "{\"path\":\"$F\",$A,\"tile\":2048,\"margin\":128,\"channels\":[2,0],\"t\":0,\"z\":0,\"workers\":4,\"cache\":1}" rgb_win_w4_nocache_c20.json
$P xyc "{\"path\":\"$F\",$A,\"planes\":[[0,0]],\"memory_limit\":2147483648}" rgb_xyc.json
$P planes "{\"path\":\"$F\",$A,\"memory_limit\":67108864}" rgb_planes_64m.json
$P planes "{\"path\":\"$F\",$A,\"memory_limit\":1073741824}" rgb_planes_1g.json
$P verify "{\"path\":\"$F\",$A,\"records\":[\"rgb_win_w1_plan.json\",\"rgb_win_w4_plan.json\",\"rgb_win_w4_nocache_c20.json\",\"rgb_xyc.json\",\"rgb_planes_64m.json\",\"rgb_planes_1g.json\"]}" rgb_verify.json
F=$ZARR529_DATA/ssbd_smovie/smovie.ome.zarr
ALLZ=$(python3 -c "print([[0,z] for z in range(58)])")
$P windows "{\"path\":\"$F\",$A,\"tile\":256,\"margin\":32,\"channels\":[0,1,2],\"t\":0,\"z\":29,\"workers\":1,\"cache\":\"plan\"}" sm_win_w1_plan.json
$P windows "{\"path\":\"$F\",$A,\"tile\":256,\"margin\":32,\"channels\":[0,1,2],\"t\":0,\"z\":29,\"workers\":4,\"cache\":\"plan\"}" sm_win_w4_plan.json
$P xyc "{\"path\":\"$F\",$A,\"planes\":$ALLZ}" sm_xyc.json > sm_xyc.log
$P planes "{\"path\":\"$F\",$A,\"memory_limit\":1073741824}" sm_planes_1g.json > sm_planes_1g.log
$P planes "{\"path\":\"$F\",$A,\"memory_limit\":4194304,\"planes\":[[0,0],[0,29],[0,57]]}" sm_planes_4m.json
$P verify "{\"path\":\"$F\",$A,\"records\":[\"sm_win_w1_plan.json\",\"sm_win_w4_plan.json\",\"sm_xyc.json\",\"sm_planes_1g.json\",\"sm_planes_4m.json\"]}" sm_verify.json
for V in v04 v05; do
F=$ZARR529_DATA/marmoset_$V/marmoset_neurons.ome.zarr; A='"axes":"ZYX"'
SOMEZ=$(python3 -c "print([[None,z] for z in (0,63,64,157,313)])" | sed 's/None/null/g')
$P windows "{\"path\":\"$F\",$A,\"tile\":256,\"margin\":32,\"channels\":[0],\"z\":157,\"workers\":1,\"cache\":\"plan\"}" mm${V}_win_w1_plan.json
$P windows "{\"path\":\"$F\",$A,\"tile\":256,\"margin\":32,\"channels\":[0],\"z\":157,\"workers\":4,\"cache\":\"plan\"}" mm${V}_win_w4_plan.json
$P xyc "{\"path\":\"$F\",$A,\"planes\":$SOMEZ}" mm${V}_xyc.json
$P planes "{\"path\":\"$F\",$A,\"memory_limit\":1073741824}" mm${V}_planes_1g.json > mm${V}_planes_1g.log
$P planes "{\"path\":\"$F\",$A,\"memory_limit\":8388608,\"planes\":$SOMEZ}" mm${V}_planes_8m.json
$P verify "{\"path\":\"$F\",$A,\"records\":[\"mm${V}_win_w1_plan.json\",\"mm${V}_win_w4_plan.json\",\"mm${V}_xyc.json\",\"mm${V}_planes_1g.json\",\"mm${V}_planes_8m.json\"]}" mm${V}_verify.json
done
uptime
echo ALLDONE
