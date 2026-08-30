"""Verifier for dynamo/repair-sft-trainer.

Every test checks conformance of `/app/sfttrainer` to `/app/SPEC.md`, which is the
requirement `instruction.md` states. Expected values are computed at verify time by
`reference.py`, an implementation of the same specification written independently of the
package under test, so no ground truth is frozen to disk or hardcoded here.

The package is executed in an isolated child process (`python -I`, `/app` the only path
entry) and communicates by JSON on stdout. It is never imported into the pytest process,
so agent code cannot reach the reference, the panels, or the test framework.
"""

import json
import pathlib
import subprocess
import sys

import pytest

TESTS = pathlib.Path(__file__).resolve().parent
PANELS = TESTS / "panels"
APP = pathlib.Path("/app")

sys.path.insert(0, str(TESTS))
import reference  # noqa: E402

# Relative tolerance for the two float-valued outputs (loss weights, learning rates).
# Both are float64 reductions whose summation order legitimately differs between
# implementations; 1e-7 sits far above that drift and far below any behavioural error.
RTOL = 1e-7

DRIVER = r"""
import json, sys
sys.path.insert(0, "/app")
from sfttrainer.pipeline import build_run
panel = json.loads(sys.stdin.read())
run = build_run(panel["conversations"], panel["config"])
print(json.dumps({
    "examples": [{"id": e["id"], "tokens": e["tokens"].tolist(),
                  "mask": e["mask"].tolist()} for e in run["examples"]],
    "microbatches": [{"example_ids": b["example_ids"], "tokens": b["tokens"].tolist(),
                      "mask": b["mask"].tolist(),
                      "loss_weight": float(b["loss_weight"])}
                     for b in run["microbatches"]],
    "steps": [{"microbatch_indices": list(s["microbatch_indices"]),
               "lr": float(s["lr"])} for s in run["steps"]],
}))
"""

_cache = {}


def panel(name):
    """Load one held-out panel input."""
    return json.loads((PANELS / f"{name}.json").read_text())


def actual(name):
    """Run the agent's package over a panel in an isolated child process."""
    if name not in _cache:
        payload = panel(name)
        proc = subprocess.run(
            [sys.executable, "-I", "-c", DRIVER],
            input=json.dumps(payload), capture_output=True, text=True, cwd="/",
        )
        assert proc.returncode == 0, (
            f"/app/sfttrainer failed on panel {name}:\n{proc.stderr.strip()[-1500:]}"
        )
        _cache[name] = json.loads(proc.stdout)
    return _cache[name]


def expected(name):
    """Reference result for a panel, computed from the specification."""
    payload = panel(name)
    run = reference.build_run_reference(payload["conversations"], payload["config"])
    return json.loads(json.dumps({
        "examples": [{"id": e["id"], "tokens": e["tokens"].tolist(),
                      "mask": e["mask"].tolist()} for e in run["examples"]],
        "microbatches": [{"example_ids": b["example_ids"], "tokens": b["tokens"].tolist(),
                          "mask": b["mask"].tolist(),
                          "loss_weight": float(b["loss_weight"])}
                         for b in run["microbatches"]],
        "steps": run["steps"],
    }))


ALL_PANELS = sorted(p.stem for p in PANELS.glob("p*.json"))


def test_repro_runs_clean():
    """The shipped reproduction in /app/repro.py completes without error."""
    proc = subprocess.run(
        [sys.executable, "-E", "-s", "repro.py"],
        capture_output=True, text=True, cwd=str(APP),
    )
    assert proc.returncode == 0, f"/app/repro.py still fails:\n{proc.stderr.strip()[-1500:]}"


def test_single_turn_assembly():
    """SPEC §5: role markers, content tokens and <end> are assembled and masked correctly."""
    assert actual("p01_single_turn")["examples"] == expected("p01_single_turn")["examples"]


def test_example_order_and_dropping():
    """SPEC §6/§9: examples with no supervision are dropped, survivors keep input order."""
    name = "p06_drop_unsupervised"
    got = [e["id"] for e in actual(name)["examples"]]
    assert got == [e["id"] for e in expected(name)["examples"]]


def test_multi_turn_supervision():
    """SPEC §5: in a two-exchange conversation, both assistant turns are supervised."""
    name = "p02_multi_turn_pair"
    got = [e["mask"] for e in actual(name)["examples"]]
    assert got == [e["mask"] for e in expected(name)["examples"]]


def test_interleaved_supervision():
    """SPEC §5: three assistant turns are supervised and the trailing user turn is not."""
    name = "p03_multi_turn_interleaved"
    got = [e["mask"] for e in actual(name)["examples"]]
    assert got == [e["mask"] for e in expected(name)["examples"]]


def test_truncation_keeps_prefix():
    """SPEC §6: an over-long example keeps the first max_seq_len tokens, not the tail."""
    name = "p04_truncate_assistant_prefix"
    got = [e["tokens"] for e in actual(name)["examples"]]
    assert got == [e["tokens"] for e in expected(name)["examples"]]


@pytest.mark.parametrize("name", ["p04_truncate_assistant_prefix", "p05_truncate_multi_turn"])
def test_truncation_keeps_arrays_aligned(name):
    """SPEC §6: tokens and mask are truncated together and stay the same length."""
    limit = panel(name)["config"]["max_seq_len"]
    for example in actual(name)["examples"]:
        assert len(example["tokens"]) == len(example["mask"])
        assert len(example["tokens"]) <= limit


def test_truncated_multi_turn():
    """SPEC §5+§6: truncation cutting into a later turn preserves earlier supervision."""
    name = "p05_truncate_multi_turn"
    assert actual(name)["examples"] == expected(name)["examples"]


def test_packing_at_exact_boundary():
    """SPEC §7: a candidate is admitted when the padded size equals the budget exactly."""
    name = "p07_pack_exact_boundary"
    got = [b["example_ids"] for b in actual(name)["microbatches"]]
    assert got == [b["example_ids"] for b in expected(name)["microbatches"]]


def test_packing_across_width_jump():
    """SPEC §7: the candidate's own width counts toward the capacity test."""
    name = "p08_pack_width_jump"
    got = [b["example_ids"] for b in actual(name)["microbatches"]]
    assert got == [b["example_ids"] for b in expected(name)["microbatches"]]


@pytest.mark.parametrize("name", ALL_PANELS)
def test_microbatch_within_token_budget(name):
    """SPEC §7: no micro-batch exceeds its padded token budget on any panel."""
    cap = panel(name)["config"]["max_tokens_per_microbatch"]
    for index, batch in enumerate(actual(name)["microbatches"]):
        rows, width = len(batch["tokens"]), len(batch["tokens"][0])
        assert rows * width <= cap, (
            f"micro-batch {index} of {name} occupies {rows}x{width} = {rows * width} "
            f"padded tokens, over the budget of {cap}"
        )


def test_padding_is_masked_out():
    """SPEC §7: rows are right-padded with <pad> and padding is never supervised."""
    name = "p08_pack_width_jump"
    for batch in actual(name)["microbatches"]:
        for tokens, mask in zip(batch["tokens"], batch["mask"]):
            body = len(tokens) - next(
                (i for i, t in enumerate(reversed(tokens)) if t != 0), len(tokens)
            )
            assert all(t == 0 for t in tokens[body:])
            assert all(m == 0 for m in mask[body:])


def test_microbatch_arrays_match_reference():
    """SPEC §7: packed token and mask arrays match the specified materialization."""
    name = "p08_pack_width_jump"
    assert actual(name)["microbatches"] == expected(name)["microbatches"]


def test_loss_weights_on_ragged_step():
    """SPEC §8: weights divide supervised tokens by the step total, not the batch count."""
    name = "p09_ragged_normalization"
    got = [b["loss_weight"] for b in actual(name)["microbatches"]]
    want = [b["loss_weight"] for b in expected(name)["microbatches"]]
    assert got == pytest.approx(want, rel=RTOL)


@pytest.mark.parametrize("name", ALL_PANELS)
def test_loss_weights_sum_to_one(name):
    """SPEC §8: the loss weights within every optimizer step sum to 1."""
    result = actual(name)
    for step in result["steps"]:
        total = sum(result["microbatches"][i]["loss_weight"]
                    for i in step["microbatch_indices"])
        assert total == pytest.approx(1.0, rel=RTOL)


def test_learning_rate_schedule():
    """SPEC §10: warmup then cosine decay, advancing once per optimizer step."""
    name = "p10_schedule_span"
    got = [s["lr"] for s in actual(name)["steps"]]
    want = [s["lr"] for s in expected(name)["steps"]]
    assert got == pytest.approx(want, rel=RTOL)
