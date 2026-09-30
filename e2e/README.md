# e2e proof pack
Setup: `.venv/bin/pip install -r requirements-dev.txt && .venv/bin/playwright install chromium`. Qwen should be up on :8089.
Run all 30 flows: `.venv/bin/python -m e2e.flows` (~15 min: a cold refresh plus live Qwen). Run a subset with `--only 2,3,9`; results merge into `docs/proof/results.json`. Then `.venv/bin/python -m e2e.proof_md` regenerates `docs/proof/PROOF.md`.
It only copies `data/finres.db` (to `/tmp/finres_proof*.db`) and serves copies on ports 8601–8612. Screenshots go to `docs/proof/`, and orange outlines mark the element each one proves.
Parity alone: `FINRES_DB=/tmp/finres_proof.db .venv/bin/python -m e2e.parity`, which needs `lab/data/lab.db`.
Flows 5–8, 14 and 21 depend on state from earlier flows in the same run.
Flow 28b fills `/tmp/finres_proof_shadow.db` with SYNTHETIC prices (the `tests/test_shadow.py` fixture over the real universe, to 2026-11-05) and runs `state.step_shadows` on it, so the shadow table can be shown before real month-ends exist. Its screenshot heading and `results.json` entry say "synthetic". Flow 30 reads the expected lab numbers from `lab/results/*.json`.
