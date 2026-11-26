set -u
cd /tmp/claude-0/-home-user/2ac2ecb9-585d-5d49-ab6c-5993039c06bf/scratchpad/zarr529/out
P="/tmp/claude-0/-home-user/2ac2ecb9-585d-5d49-ab6c-5993039c06bf/scratchpad/zarr529/venv/bin/python -I /tmp/claude-0/-home-user/2ac2ecb9-585d-5d49-ab6c-5993039c06bf/scratchpad/zarr529/scripts/lazy529.py"
F=/tmp/claude-0/-home-user/2ac2ecb9-585d-5d49-ab6c-5993039c06bf/scratchpad/zarr529/data/ebi_cy5/cy5.ome.zarr
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
