#!/bin/bash
# Runs inside the environment image. All verifier deps are baked into
# environment/Dockerfile, so nothing is installed here.
# The reward is this process's exit status.
set -u

mkdir -p /logs/verifier
rm -f /logs/verifier/reward.txt

cd /
env -u PYTHONPATH -u PYTHONHOME -u PYTHONSTARTUP \
    PATH=/usr/local/bin:/usr/bin:/bin \
    PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 \
    pytest --ctrf /logs/verifier/ctrf.json \
           --rootdir=/tests --confcutdir=/tests --noconftest -p no:cacheprovider \
           /tests/test_outputs.py -rA
status=$?

if [ "$status" -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi

exit "$status"
