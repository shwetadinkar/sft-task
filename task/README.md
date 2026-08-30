# dynamo/repair-sft-trainer — development notes

Reviewer context. Not passed to the agent.

## Why the task is shaped this way

The package ships four violations of `SPEC.md`. One crashes `repro.py` on the first run;
three are latent under the shipped fixtures. The latent three are the task. The visible
crash exists to give a solver something to find, fix, and feel finished with — a solver
who stops there leaves the package non-conforming and fails five verifier cases.

Dormancy is a property of the fixtures, and it is fragile: the shipped conversations must
stay single-turn (or the multi-turn masking defect fires), stay under `max_seq_len` (or
truncation fires), and stay length-uniform enough that both micro-batches carry equal
supervised token counts (or the normalization defect fires). Any edit to
`environment/data/fixtures/` can silently destroy this. `tools/check_dormancy.py` proves
it mechanically by running the shipped fixtures through a bait-repaired build with and
without the three pipeline fixes and requiring byte-identical output. Run it after
touching anything under `environment/data/`.

## Ground truth is independently derived

`tests/reference.py` was written from `SPEC.md` alone, by an author given only the
specification and the ten panel inputs — no sight of `environment/data/sfttrainer/`,
`solution/`, or any description of the defects. This is deliberate: a reference written
with the package in view reproduces the package's reading of the spec, including its
misreadings, and the verifier then only confirms the package agrees with itself.

That process also produced twelve points where the specification did not determine the
answer. Five were real gaps and were closed by tightening `SPEC.md` (the whitespace
class and contentless-turn edges are now excluded by the §2 input guarantees; §7 states
that a candidate always joins an empty micro-batch and uses lexicographic tie-breaking;
§9 says which length `L` denotes). The remainder were caller obligations or corners where
both implementations already agreed. The reference and the repaired package agree byte
for byte on all ten panels.

## Panels

Six of ten discriminate the shipped package; four pin rules the defects happen to
satisfy. The conformance-only four are kept because a solver who rewrites a module rather
than patching it can regress them, which keeps the suite grading conformance rather than
the presence of four specific edits. `tools/check_panels.py` asserts which panels fall in
which group, so a later edit that neuters a discriminating panel fails loudly instead of
passing quietly.

## Gotchas

- `python -I` implies `-P`, which strips the script's directory from `sys.path`. The
  panel driver passes `/app` explicitly so this is fine, but the `repro.py` invocation in
  `tests/test_outputs.py` uses `-E -s` instead, because a script run under `-I` cannot
  import the package sitting beside it.
- `SPEC.md` §10 holds the peak learning rate across the warmup/decay seam: `t = W-1`
  ends warmup at `base_lr` and `t = W` enters cosine at progress 0, also `base_lr`. Two
  consecutive steps at the peak in the schedule panel are correct, not an off-by-one.
- The upstream `tests/test.sh` template ends on an `if` whose exit status is always 0, so
  the process status never reflects the pytest result. This copy captures the status and
  exits with it, while still writing `/logs/verifier/reward.txt`.

## Local gate

    bash tools/validate.sh

Checks taxonomy labels against the controlled vocabulary, the pinned base image, that the
environment image carries no ground truth and installs nothing at verify time, dormancy,
panel behaviour, and the reward contract in both directions (no-op writes 0, oracle
writes 1). `tools/build_panels.py` regenerates the panel inputs; its `cfg()` helper
asserts the §3 precondition, which is worth keeping if panels are ever added.
