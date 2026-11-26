set -u
cd /tmp/claude-0/-home-user/2ac2ecb9-585d-5d49-ab6c-5993039c06bf/scratchpad/zarr529/out
P="/tmp/claude-0/-home-user/2ac2ecb9-585d-5d49-ab6c-5993039c06bf/scratchpad/zarr529/venv/bin/python -I /tmp/claude-0/-home-user/2ac2ecb9-585d-5d49-ab6c-5993039c06bf/scratchpad/zarr529/scripts/lazy529.py"
F=/tmp/claude-0/-home-user/2ac2ecb9-585d-5d49-ab6c-5993039c06bf/scratchpad/zarr529/data/ssbd_rgb/brainslice.ome.zarr; A='"axes":"TCZYX"'
uptime
$P windows "{\"path\":\"$F\",$A,\"tile\":2048,\"margin\":128,\"channels\":[0,1,2],\"t\":0,\"z\":0,\"workers\":1,\"cache\":\"plan\"}" rgb_win_w1_plan.json
$P windows "{\"path\":\"$F\",$A,\"tile\":2048,\"margin\":128,\"channels\":[0,1,2],\"t\":0,\"z\":0,\"workers\":4,\"cache\":\"plan\"}" rgb_win_w4_plan.json
$P windows "{\"path\":\"$F\",$A,\"tile\":2048,\"margin\":128,\"channels\":[2,0],\"t\":0,\"z\":0,\"workers\":4,\"cache\":1}" rgb_win_w4_nocache_c20.json
$P xyc "{\"path\":\"$F\",$A,\"planes\":[[0,0]],\"memory_limit\":2147483648}" rgb_xyc.json
$P planes "{\"path\":\"$F\",$A,\"memory_limit\":67108864}" rgb_planes_64m.json
$P planes "{\"path\":\"$F\",$A,\"memory_limit\":1073741824}" rgb_planes_1g.json
$P verify "{\"path\":\"$F\",$A,\"records\":[\"rgb_win_w1_plan.json\",\"rgb_win_w4_plan.json\",\"rgb_win_w4_nocache_c20.json\",\"rgb_xyc.json\",\"rgb_planes_64m.json\",\"rgb_planes_1g.json\"]}" rgb_verify.json
F=/tmp/claude-0/-home-user/2ac2ecb9-585d-5d49-ab6c-5993039c06bf/scratchpad/zarr529/data/ssbd_smovie/smovie.ome.zarr
ALLZ=$(python3 -c "print([[0,z] for z in range(58)])")
$P windows "{\"path\":\"$F\",$A,\"tile\":256,\"margin\":32,\"channels\":[0,1,2],\"t\":0,\"z\":29,\"workers\":1,\"cache\":\"plan\"}" sm_win_w1_plan.json
$P windows "{\"path\":\"$F\",$A,\"tile\":256,\"margin\":32,\"channels\":[0,1,2],\"t\":0,\"z\":29,\"workers\":4,\"cache\":\"plan\"}" sm_win_w4_plan.json
$P xyc "{\"path\":\"$F\",$A,\"planes\":$ALLZ}" sm_xyc.json > sm_xyc.log
$P planes "{\"path\":\"$F\",$A,\"memory_limit\":1073741824}" sm_planes_1g.json > sm_planes_1g.log
$P planes "{\"path\":\"$F\",$A,\"memory_limit\":4194304,\"planes\":[[0,0],[0,29],[0,57]]}" sm_planes_4m.json
$P verify "{\"path\":\"$F\",$A,\"records\":[\"sm_win_w1_plan.json\",\"sm_win_w4_plan.json\",\"sm_xyc.json\",\"sm_planes_1g.json\",\"sm_planes_4m.json\"]}" sm_verify.json
for V in v04 v05; do
F=/tmp/claude-0/-home-user/2ac2ecb9-585d-5d49-ab6c-5993039c06bf/scratchpad/zarr529/data/marmoset_$V/marmoset_neurons.ome.zarr; A='"axes":"ZYX"'
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
