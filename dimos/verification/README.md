# Verification funnel

Machine-checkable primitives for the hardware verification funnel described in
[`docs/development/hardware_verification_loop.md`](/docs/development/hardware_verification_loop.md):
task specs, a multi-signal judge, a safety envelope check, and failure
evidence bundles.

- `task_spec.py` — versioned, machine-checkable task definitions (YAML), the
  common acceptance criteria used to judge both simulation and hardware trials.
- `judge.py` — evaluates a task spec's predicates against per-trial signal
  reports (proprioception, external camera, ...); an optional VLM opinion is
  recorded for diagnosis but never overrides a predicate-based verdict.
- `safety_governor.py` — static safety-envelope checks on a commanded
  trajectory, independent of the code being checked.
- `evidence.py` — packages a failed verification result into a structured,
  replayable evidence bundle for the next diagnose-fix-redeploy cycle.

This module is pure logic today (no simulator/camera/robot wiring) so it can
be exercised by fast unit tests (`test_judge.py`) and reused unchanged by
both the sim-regression stage and the real-hardware-trial stage of the
funnel — see the design doc for the rollout phases that wire it up.
