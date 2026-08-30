#!/bin/bash
# Oracle solution: repair sfttrainer so it conforms to /app/SPEC.md.
#
# Four defects, one visible and three latent under the shipped fixtures:
#   batching.py  §7  capacity test omits the candidate's width from the running
#                    maximum, so a micro-batch overruns the padded token budget
#                    whenever a longer example follows shorter ones.
#   pipeline.py  §5  only the final assistant turn is supervised; every earlier
#                    assistant turn is masked out.
#   pipeline.py  §6  over-long examples keep the token tail but the mask head,
#                    desynchronising the two arrays.
#   pipeline.py  §8  the loss weight divides by the micro-batch count instead of
#                    the step's supervised token total.
#
# schedule.py already conforms and is left untouched.
set -euo pipefail

cd "$(dirname "$0")"

install -m 644 pipeline.py /app/sfttrainer/pipeline.py
install -m 644 batching.py /app/sfttrainer/batching.py

cd /app
python repro.py
