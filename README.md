# UI-Physician

An agent skill that diagnoses and repairs Android layout-inflation and
ConstraintLayout runtime failures. It works from a raw Logcat dump and the
offending layout XML, and it verifies every fix through a bounded
self-correction loop.

```text
Intake ─▶ Analysis ─▶ Hypothesis ─▶ Execution ─▶ Verification ──▶ RESOLVED ─▶ Report
             ▲             ▲                          │
             │             └── MUTATED_PROGRESS ──────┤  (promote patch, retarget)
             └──── PERSISTED / MUTATED_REGRESSION ────┘  (backtrack, widen scope)
                   scope: LOCAL → HIERARCHY → ENVIRONMENT · max 3 iterations → escalate
```

## Repository layout

| Path | Contents |
|---|---|
| [`SKILL.md`](SKILL.md) | The skill specification: scope, input contract, session state, workflow, self-correction loop, verification, edge cases, output contract |
| [`references/error-signatures.md`](references/error-signatures.md) | Log signature → scoped causes → canonical fixes, per failure family |
| [`scripts/fingerprint.py`](scripts/fingerprint.py) | Crash fingerprint extractor and verdict classifier (stdlib only) |
| [`examples/constraintset-missing-id/`](examples/constraintset-missing-id/walkthrough.md) | End-to-end session with a failed first attempt and a backtrack |
| [`tests/`](tests/test_fingerprint.py) | Unit tests for the fingerprint script |

## Design decisions

- **Fingerprints, not string matching, decide failure.** A crash is "the same"
  when its deepest cause, normalized message, layout, inflating class and
  app frame match. XML line numbers are excluded, so a patch that shifts
  lines is not mistaken for progress.
- **Every retry changes something.** A retry must widen the scope, add
  evidence, or retire an assumption. Failed patches are reverted, and the
  next one is re-derived from the immutable baseline.
- **Progress is not failure.** When a fix unmasks the next defect downstream
  (`MUTATED_PROGRESS`), the patch is kept and the loop retargets. Oscillation
  between two fingerprints escalates immediately.
- **Claims follow the evidence.** Static checks alone can only produce
  "pending runtime confirmation". `RESOLVED` requires a post-fix capture that
  shows the screen was reached.

## Using the fingerprint script

```sh
# Fingerprint a crash
python scripts/fingerprint.py logcat.txt --app-package com.example.shop

# Classify a post-fix capture against the baseline
python scripts/fingerprint.py --compare logcat.txt attempt-1.txt --app-package com.example.shop
```

Capture logs with:

```sh
adb logcat -c
adb shell am start -W -n <package>/<activity>
adb logcat -d -v threadtime -b main,crash > logcat.txt
```

## Running the tests

```sh
python -m unittest discover -s tests -v
```

Requires Python 3.9+ with no third-party dependencies.

## Installing as a Claude Code skill

Copy or symlink this repository into a skills directory, e.g.
`~/.claude/skills/ui-physician/` (personal) or `.claude/skills/ui-physician/`
(project). The frontmatter `description` in `SKILL.md` tells the agent when
to load it.
