#!/bin/bash
# Local gate for dynamo/repair-sft-trainer. Run from the repository root.
#
#   bash tools/validate.sh
#
# Checks, in order:
#   1. task.toml parses and its taxonomy labels are in the controlled vocabulary
#   2. the base image is the pre-approved pinned digest
#   3. the environment image contains no ground truth
#   4. the shipped fixtures cannot distinguish the dormant defects
#   5. every held-out panel discriminates, or is a declared conformance panel
#   6. no-op run  -> verifier writes 0
#   7. oracle run -> verifier writes 1
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
FAILED=0

step() { printf '\n=== %s ===\n' "$1"; }
fail() { echo "FAIL: $1"; FAILED=1; }

step "1. task.toml schema and taxonomy labels"
python3 - <<'PY' || FAILED=1
import pathlib, sys, tomllib
manifest = tomllib.loads(pathlib.Path("task/task.toml").read_text())
taxonomy = tomllib.loads(pathlib.Path("references/diversity-taxonomy.toml").read_text())

def norm(s):
    return s.lower().replace(" ", "_").replace("-", "_")

meta = manifest["metadata"]
ok = True

categories = {norm(k): v for k, v in taxonomy["categories"].items()}
category = norm(meta["category"])
if category not in categories:
    print(f"FAIL category {meta['category']!r} not in taxonomy"); ok = False
elif norm(meta["subcategory"]) not in {norm(s) for s in categories[category]}:
    print(f"Warning subcategory {meta['subcategory']!r} not listed under {meta['category']!r}")

for field in ("task_objective", "artifact_type"):
    allowed = {norm(v) for v in taxonomy[field]}
    values = meta[field]
    if not values:
        print(f"FAIL {field} is empty"); ok = False
    for value in values:
        if norm(value) not in allowed:
            print(f"FAIL {field} value {value!r} not in closed set"); ok = False

if meta.get("expert_time_estimate_hours", 0) == 0:
    print("FAIL expert_time_estimate_hours is 0"); ok = False
for field in ("difficulty_explanation", "solution_explanation", "verification_explanation"):
    if not meta.get(field, "").strip():
        print(f"FAIL {field} is empty"); ok = False

if "/" not in manifest["task"]["name"]:
    print("FAIL task.name is not in org/name form"); ok = False
if "task" in manifest and not isinstance(manifest["task"], dict):
    print("FAIL root-level task string present"); ok = False
if not manifest.get("artifacts"):
    print("FAIL artifacts is empty"); ok = False

print("task.toml OK" if ok else "task.toml has problems")
sys.exit(0 if ok else 1)
PY

step "2. base image policy"
bash references/check-base-image.sh task || fail "base image not pre-approved"

step "3. environment image carries no ground truth"
if grep -qiE '^[[:space:]]*COPY[[:space:]].*(solution|tests)' task/environment/Dockerfile; then
  fail "Dockerfile COPYs solution/ or tests/"
else
  echo "Dockerfile COPYs data only — OK"
fi
if grep -qiE '(apt-get|pip install|curl|uvx)' task/tests/test.sh; then
  fail "test.sh installs or fetches tooling at verify time"
else
  echo "test.sh installs nothing — OK"
fi

step "4. dormancy of the pipeline defects under the shipped fixtures"
python3 tools/check_dormancy.py || fail "shipped fixtures expose a dormant defect"

step "5. held-out panels"
python3 tools/check_panels.py || fail "a panel is not behaving as designed"

step "6/7. no-op and oracle execution"
if [ -w / ] || [ -w /app ]; then
python3 - <<'PY' || FAILED=1
import pathlib, shutil, subprocess, sys, tempfile

root = pathlib.Path(".").resolve()

def verify(apply_oracle):
    work = pathlib.Path(tempfile.mkdtemp())
    app = work / "app"
    shutil.copytree(root / "task" / "environment" / "data", app)
    if apply_oracle:
        for name in ("pipeline.py", "batching.py"):
            shutil.copy(root / "task" / "solution" / name, app / "sfttrainer" / name)
    # the verifier addresses the package at /app, so stage it there
    live = pathlib.Path("/app")
    if live.exists():
        shutil.rmtree(live)
    shutil.copytree(app, live)
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider",
         str(root / "task" / "tests" / "test_outputs.py")],
        capture_output=True, text=True,
    )
    return 1 if out.returncode == 0 else 0

nop = verify(False)
oracle = verify(True)
print(f"no-op reward  = {nop} (expected 0)")
print(f"oracle reward = {oracle} (expected 1)")
sys.exit(0 if (nop, oracle) == (0, 1) else 1)
PY
[ $? -eq 0 ] || fail "reward contract violated"
else
  echo "SKIPPED: /app is not writable by this user."
  echo "  The verifier addresses the package at /app, so this check needs a writable /app."
  echo "  Do not re-run this script under sudo — root's interpreter will not have the"
  echo "  venv's numpy and pytest, and checks 4 and 5 will fail spuriously."
  echo "  Run the equivalent check under Docker instead (see task/README.md)."
fi

printf '\n'
if [ "$FAILED" -eq 0 ]; then
  echo "validate.sh: all checks passed"
else
  echo "validate.sh: FAILURES above"
fi
exit "$FAILED"
