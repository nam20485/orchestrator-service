# orchestrator_service manual run skill

```sh

# skill argument
$image_ref

# start the services 
./scripts/dc.ps1 up `$image_ref` && ./scripts/dc.ps1 logs `$image_ref` -f

# enable tailscale proxy to recv webhook
sudo tailscale funnel --bg 80

# check status
# sudo tailscale funnel status

# create a new repo from the plan_docs in the directory named `$Slug`,
# create a new issue in the repo, and create a new workflow run for the issue
 ~/src/github/nam20485/src/github/nam20485/workflow_launch2/scripts/create-repo-from-slug.ps1 -Slug gap-miner-v2 -Owner nam20485 -Yes

# then...

 # 1. monitor the workfow until completion
 # 2. analyze and create ananylsis trace document, e.g.  `traces/gap-miner-v2-delta48-373i6gxj.md`
```
