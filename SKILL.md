---
name: ui-physician
description: >
  Diagnose and repair Android layout-inflation and ConstraintLayout runtime
  failures from a raw Logcat dump plus the offending layout XML. Use when a
  crash log contains InflateException, "Error inflating class",
  "Binary XML file line #", a ConstraintLayout/ConstraintSet exception, or a
  LayoutParams ClassCastException. Runs a bounded self-correction loop
  (max 3 iterations) that verifies every fix against the log, backtracks on
  failure, and escalates with an evidence report when it cannot converge.
---

# UI-Physician

## 1. Skill Name & Purpose

| Field | Value |
|---|---|
| Name | `ui-physician` |
| Domain | Android View-system runtime failures (XML inflation, ConstraintLayout) |
| Input | Raw Logcat dump + layout XML (+ optional supporting sources) |
| Output | Diagnosis, minimal unified-diff patch, verification verdict, iteration ledger |
| Loop bound | 3 fix iterations, then escalation |

UI-Physician turns a crash log into a *verified* layout fix. It is not a
pattern-matching autocomplete: every patch is a falsifiable hypothesis with a
predicted log outcome, and the agent is required to prove or disprove that
prediction before it may claim success.

### 1.1 Scope

**In scope** — the agent MUST handle:

| Code | Failure family | Typical log signature |
|---|---|---|
| `INF-CLASS` | View class cannot be loaded | `Error inflating class X` → `ClassNotFoundException` |
| `INF-CTOR` | Custom view missing XML constructor | `NoSuchMethodException: X.<init> [class android.content.Context, interface android.util.AttributeSet]` |
| `INF-RES` | Referenced resource missing for this device config | `Resources$NotFoundException` |
| `INF-THEME` | Attribute or component requires a theme the context lacks | `Failed to resolve attribute at index N`, `requires your app theme to be Theme.MaterialComponents` |
| `INF-ATTR` | Attribute value of the wrong type / missing mandatory attribute | `Can't convert value at index N to dimension`, `You must supply a layout_width attribute` |
| `INF-MERGE` | `<merge>` inflated without a parent | `<merge /> can be used only with a valid ViewGroup root and attachToRoot=true` |
| `INF-PARENT` | Inflated view attached twice | `The specified child already has a parent` |
| `INF-FRAG` | Static `<fragment>` / `FragmentContainerView` inflation errors | `Error inflating class fragment`, `Duplicate id` |
| `CL-IDS` | ConstraintSet used on children without ids | `All children of ConstraintLayout must have ids to use ConstraintSet` |
| `CL-PARAMS` | Wrong LayoutParams type inside/outside ConstraintLayout | `ClassCastException: ...$LayoutParams cannot be cast to ...ConstraintLayout$LayoutParams` |
| `CL-SILENT` | Constraint defects with no exception (view jumps to 0,0, collapses, overlaps) | No crash; only a user-described visual symptom or `W/` lines |

The full signature catalog, with causes and canonical fixes, lives in
[`references/error-signatures.md`](references/error-signatures.md).

**Out of scope** — the agent MUST hand off (and say so) instead of guessing:

- Crashes whose deepest `Caused by` is not produced by inflation, layout
  params, or ConstraintLayout (e.g. a `NullPointerException` in business
  logic that merely happens after `setContentView`).
- Jetpack Compose runtime errors (no XML inflation takes place).
- Native crashes (`F/libc`, tombstones), ANRs, and `OutOfMemoryError` from
  bitmap decoding — these need different evidence (tombstones, traces.txt,
  heap dumps).
- Performance-only complaints (overdraw, deep hierarchies) without a failure.

## 2. Input Requirements

### 2.1 Logcat

Accepted line formats (auto-detected per line; unprefixed lines are treated
as continuations of the previous record):

| Format | Example prefix | Source |
|---|---|---|
| `threadtime` (preferred) | `09-30 14:12:01.123 12345 12345 E AndroidRuntime: ` | `adb logcat -v threadtime` |
| `brief` | `E/AndroidRuntime(12345): ` | `adb logcat -v brief` |
| Android Studio | `2026-09-30 14:12:01.123 12345-12345 AndroidRuntime com.example E ` | Logcat panel copy |
| Raw trace | `java.lang.RuntimeException: ...` | Pasted stack trace only |

Minimum viable evidence: the complete `FATAL EXCEPTION` block from
`AndroidRuntime`, including **every** `Caused by:` line. Recommended capture:

```sh
adb logcat -c
adb shell am start -W -n <package>/<activity>
adb logcat -d -v threadtime -b main,crash > logcat.txt
```

### 2.2 Layout XML

- The **source** layout named in the trace (`... in <package>:layout/<name>`),
  never the compiled/binary XML from an APK.
- Every layout pulled in by `<include layout="...">` on the path to the failing
  line, and the root of any `<merge>` target.
- When the failure names a custom class (`INF-CLASS`, `INF-CTOR`): that class's
  source file.
- When the failure is theme-related (`INF-THEME`, `INF-ATTR` via `?attr/`):
  `themes.xml`/`styles.xml` and the `android:theme` of the hosting
  `<activity>` in `AndroidManifest.xml`.
- When the failure is thrown from code (`CL-IDS`, `CL-PARAMS`, `INF-PARENT`):
  the calling snippet (the first app frame in the trace).

### 2.3 Optional context (raises confidence, never required)

`minSdk`/device API level, `androidx.constraintlayout` and
`com.google.android.material` versions, whether Data Binding or View Binding
is enabled, and the R8 `mapping.txt` for release builds.

### 2.4 Intake gate

Run these checks before any analysis. A failed **blocking** check stops the
skill with a precise request for the missing artifact.

| # | Check | Blocking? | On failure |
|---|---|---|---|
| G1 | Log contains an `AndroidRuntime` fatal block or a raw exception | yes | Ask for the capture commands in §2.1 |
| G2 | Deepest `Caused by` is present (no trailing `... N more` hiding the root) | no | Continue; flag `TRUNCATED` (see §7) |
| G3 | Layout named in the trace was supplied | yes (for `INF-*`) | Request that exact file by resource name |
| G4 | Frames are not obfuscated (`a.b.c(Unknown Source)`) | no | Ask for `mapping.txt` / `retrace`; continue with XML-only evidence |
| G5 | Log and XML belong to the same build (line in trace exists in XML and lands on a view tag) | no | Flag `VERSION_SKEW` (see §7) |
| G6 | Deepest cause maps to an in-scope family (§1.1) | yes | Hand off with the reason |

## 3. Session State

The agent keeps one explicit state object for the whole session and updates it
at the end of every phase. Nothing in the loop may depend on memory that is not
written here.

```yaml
session:
  baseline:                # immutable after Phase 0
    xml: {<file>: <original contents>}
    fingerprint: <Fingerprint>
  iteration: 0             # incremented when a patch is emitted
  max_iterations: 3
  scope: LOCAL             # LOCAL -> HIERARCHY -> ENVIRONMENT (never goes back)
  active_patch: null       # unified diff currently under verification
  assumptions: []          # facts taken as true without evidence, e.g. "include root has an id"
  ledger:                  # one entry per iteration, append-only
    - iteration: 1
      scope: LOCAL
      hypothesis: "<claim>"
      evidence: ["<log token>", "<file:line>"]
      assumptions: ["A1"]
      predicted_outcome: RESOLVED | MUTATED(<expected next error>)
      patch: "<diff>"
      observed_fingerprint: <Fingerprint | null>
      verdict: RESOLVED | PERSISTED | MUTATED_PROGRESS | MUTATED_REGRESSION | UNVERIFIED
      rejected_because: "<one line>"
```

### 3.1 Crash fingerprint

The fingerprint is how the agent decides whether "the same crash" happened
again. It is computed by [`scripts/fingerprint.py`](scripts/fingerprint.py)
when a shell is available, or by hand using the same rules:

| Field | Source | Normalization |
|---|---|---|
| `root_exception` | Deepest `Caused by:` class (or top exception if none) | — |
| `root_message` | Its message | Hex ids → `0x?`, object hashes → `@?`, `DexPathList[...]` collapsed, XML line numbers → `#?` |
| `layout` | Deepest `in <pkg>:layout/<name>` | — |
| `inflating_class` | Deepest `Error inflating class <X>` | — |
| `app_frame` | First non-framework frame, searched from the root cause outward | Source line number dropped |
| `id` | SHA-1 of the five fields above | — |

`xml_line` is recorded **alongside** the fingerprint but is deliberately not
part of `id`: a patch that adds or removes lines above the failure shifts the
line number without changing the defect, and must not be misread as progress.

## 4. Reasoning Workflow

`Intake → Analysis → Hypothesis → Execution → Verification → (Loop | Report)`

### Phase 0 — Intake

1. Run the intake gate (§2.4). Stop on any blocking failure.
2. Isolate the crash block: the **last** `FATAL EXCEPTION` in the dump, then
   only lines from the same PID (threadtime/Studio) — other processes'
   output is noise.
3. Compute and freeze `baseline.fingerprint` and `baseline.xml`.

### Phase 1 — Analysis

1. **Unwind the cause chain.** List every exception from outermost to deepest.
   The deepest `Caused by` is the *mechanism*; outer `InflateException`s are
   only the *location*. Never diagnose from the outermost line alone.
2. **Locate.** From the deepest message containing
   `Binary XML file line #N in <pkg>:layout/<name>`, open `<name>.xml` and
   find the element whose start tag spans line `N`. If the failure is thrown
   from code, locate the first app frame instead and map it to the layout it
   inflates or binds.
3. **Classify.** Match the deepest cause against the signature catalog and
   assign exactly one family code (§1.1). If two families match, record both
   and go to differential diagnosis (§7.2).
4. **Build the evidence map.** For the located element, record: tag/class,
   `android:id`, `style`, `android:theme` on it and on every ancestor,
   `layout_*` attributes, and every `@`/`?attr` reference with whether it
   resolves in the supplied files.

Output of Phase 1: `{family, location, evidence_map, open_questions}`.

### Phase 2 — Hypothesis

1. Generate candidate root causes **for the current `scope` only**:

   | Scope | Search space |
   |---|---|
   | `LOCAL` | The located element and its own attributes |
   | `HIERARCHY` | Ancestors, siblings, `<include>`/`<merge>` targets, styles and theme overlays inherited through the view tree, constraint graph of the parent ConstraintLayout |
   | `ENVIRONMENT` | Activity theme in the manifest, resource qualifiers (`-v21`, `-night`, `-land`), dependency presence/versions, Data/View Binding preprocessing, R8 keep rules |

2. Each hypothesis MUST cite at least one log token and one file location, and
   MUST state a predicted outcome (`RESOLVED`, or the specific next error it
   expects to unmask).
3. Discard any hypothesis already present in `ledger` with a rejected verdict
   (compare by claim and by patch effect, not by wording).
4. Rank by `specificity × evidence strength`; ties go to the smaller patch.
   Every unverified fact the top hypothesis relies on is appended to
   `assumptions`.

### Phase 3 — Execution

1. **Always patch the baseline**, never the previous attempt. The one
   exception is the `MUTATED_PROGRESS` path (§5.3), where the previous patch
   is promoted into a new baseline.
2. Patch rules:
   - One root cause per patch; the smallest edit that falsifies the hypothesis.
   - No reformatting, reordering, or renaming outside the edited element.
   - Never delete a view, constraint, or id to silence a crash.
   - Preserve existing `android:id`s (code and bindings depend on them).
   - Prefer XML fixes; touch code only when the trace's app frame is the
     defect (e.g. a missing constructor, a wrong `LayoutParams` cast).
3. Emit the patch as a unified diff, set `active_patch`, increment
   `iteration`, and write the ledger entry (verdict pending).

### Phase 4 — Verification

Run the verification strategy (§6) to obtain a verdict, then hand control to
the self-correction loop (§5).

### Phase 5 — Report

Produce the output contract (§8) for `RESOLVED`, or the escalation report
(§5.5) for any other exit.

## 5. Self-Correction Logic

### 5.1 Loop controller

```text
function run_ui_physician(log, files):
    intake(log, files)                          # Phase 0; may STOP
    analysis = analyze(baseline)                # Phase 1
    loop:
        if iteration == max_iterations:
            return escalate(EXHAUSTED)
        h = hypothesize(analysis, scope, ledger)   # Phase 2
        if h is None:                              # search space empty at this scope
            if scope == ENVIRONMENT:
                return escalate(NO_HYPOTHESIS)
            scope = widen(scope); continue         # widening is free: no iteration consumed
        patch = execute(h, baseline)               # Phase 3; iteration += 1
        verdict, fp = verify(patch)                # Phase 4 / §6
        if verdict in (MUTATED_PROGRESS, MUTATED_REGRESSION) and fp.id in ledger.seen_ids:
            return escalate(OSCILLATION)           # fix A unmasks B, fix B brings back A
        ledger.record(h, patch, fp, verdict)

        switch verdict:
            RESOLVED:           return report(SUCCESS)
            UNVERIFIED:         return report(PENDING_RUNTIME_CONFIRMATION)
            MUTATED_PROGRESS:   baseline = apply(baseline, patch)
                                baseline.fingerprint = fp
                                analysis = analyze(baseline)     # new target, same scope
            PERSISTED:          backtrack(h); scope = widen(scope)
            MUTATED_REGRESSION: backtrack(h); scope = widen(scope)
```

`ledger.seen_ids` holds the baseline fingerprint and every fingerprint
observed in earlier iterations. `widen` saturates at `ENVIRONMENT`.

### 5.2 Exit conditions

| Exit | Trigger | Result |
|---|---|---|
| `SUCCESS` | Verdict `RESOLVED` with Tier-2 evidence (§6.2) | Report with patch |
| `PENDING_RUNTIME_CONFIRMATION` | Tier-1 checks pass but no post-fix log can be obtained | Report with patch, explicitly labelled unconfirmed |
| `EXHAUSTED` | `iteration == 3` without `RESOLVED` | Escalate |
| `NO_HYPOTHESIS` | Search space empty at `ENVIRONMENT` scope | Escalate |
| `OSCILLATION` | A previously seen fingerprint returns (fix A unmasks B, fix B brings back A) | Escalate immediately |
| `OUT_OF_SCOPE` | New deepest cause belongs to no in-scope family | Hand off with the partial fix that got there |
| `BLOCKED` | Intake gate or a required file request unanswered | Stop with the precise request |

### 5.3 How the agent knows it failed

The verdict is a pure function of the baseline fingerprint `B` and the
post-patch capture `C`:

| Condition | Verdict | Meaning |
|---|---|---|
| `C` has no `FATAL EXCEPTION` for the app PID and the target layout/screen rendered (e.g. `Displayed <activity>` line) | `RESOLVED` | Fix confirmed |
| `C.id == B.id` | `PERSISTED` | Hypothesis falsified |
| `C.id != B.id` and the new failure is **downstream** of the old one: same layout at a later element, a layout inflated later in the same flow, or the same element failing on a *different attribute* | `MUTATED_PROGRESS` | Fix worked; it unmasked the next defect |
| `C.id != B.id` otherwise (earlier element, unrelated screen, new exception introduced by the edited element itself) | `MUTATED_REGRESSION` | Fix broke something |
| No capture available | `UNVERIFIED` | Fall back to Tier-1 only |

### 5.4 Retry logic — what changes on each attempt

A retry is never "try harder with the same idea". Each iteration changes at
least one of: **scope**, **evidence**, or **assumption set**.

1. **Backtrack** (`PERSISTED`, `MUTATED_REGRESSION`):
   - Revert `active_patch`; the next patch is re-derived from `baseline`.
   - Mark the hypothesis rejected with a one-line reason in the ledger.
   - Invalidate every assumption the rejected hypothesis introduced and turn
     each into an explicit evidence request or a check (e.g. assumption
     "the included root has an id" → request the included file).
   - Widen `scope` by one level and re-run Phase 1 **step 4 exhaustively**
     over the new search space: enumerate every node/attribute against the
     violated invariant instead of stopping at the first suspect.
2. **Promote** (`MUTATED_PROGRESS`):
   - Keep the patch; it becomes part of the new baseline.
   - Retarget analysis at the new fingerprint without widening scope.
   - The iteration still counts toward the limit: three chained defects
     that cannot be cleared in three patches are escalated, not chased.
3. **Iteration strategy by attempt number:**

   | Attempt | Default scope | Required change vs. previous attempt |
   |---|---|---|
   | 1 | `LOCAL` | — (most specific hypothesis from the deepest cause) |
   | 2 | `HIERARCHY` | Previous hypothesis rejected; ancestors, includes, styles, and the parent's constraint graph scanned exhaustively; assumptions from attempt 1 resolved or re-requested |
   | 3 | `ENVIRONMENT` | Manifest theme, qualifiers, dependency versions, binding/R8 build transforms examined; if still ambiguous, ask a discriminating question (§7.2) *before* spending the attempt |

### 5.5 Escalation report

```markdown
## UI-Physician: escalation (<EXIT_CODE>)
**Target crash:** <root_exception>: <root_message> @ <layout>:<line>
**Iterations used:** <n>/3   **Final scope:** <scope>

| # | Scope | Hypothesis | Patch (summary) | Verdict | Why rejected |
|---|---|---|---|---|---|
| 1 | LOCAL | ... | ... | PERSISTED | ... |

**What is now known:** <facts the loop established, including falsified causes>
**Open assumptions:** <unverified facts that still matter>
**Next evidence needed:** <the single most discriminating artifact or experiment>
**Safe partial fix (if any):** <diff of MUTATED_PROGRESS patches, else "none">
```

## 6. Verification Strategy

Success is confirmed in two tiers. Tier 1 is necessary; only Tier 2 is
sufficient for `RESOLVED`.

### 6.1 Tier 1 — static trace conformance (always runs)

Before a patch is emitted, it must pass all of:

1. **Coverage:** the patch edits the element located in Phase 1 (or, in
   `HIERARCHY`/`ENVIRONMENT` scope, a node on its ancestor/include/theme path)
   and addresses the exact token in the deepest cause (class name, attribute
   index/name, resource name, missing id).
2. **Resolution:** every `@type/name` and `?attr/name` the patch introduces
   resolves in the supplied files or in a declared dependency; no invented
   resources.
3. **Invariant replay** for the family, e.g.:
   - `INF-CLASS`: tag FQCN == `package` + class name in the supplied source.
   - `INF-CTOR`: a `(Context, AttributeSet)` constructor exists (or
     `@JvmOverloads` covers it).
   - `CL-IDS`: every direct child of the ConstraintLayout — including
     `<include>` roots and `<merge>` children — ends up with an id.
   - `CL-SILENT`: every child has ≥1 horizontal and ≥1 vertical constraint,
     every referenced id is a sibling, and no chain is circular.
4. **Blast radius:** the diff does not change unrelated elements and does not
   remove ids referenced elsewhere in the supplied sources.

### 6.2 Tier 2 — runtime reproduction

If the agent has shell access to a build + device/emulator:

```sh
./gradlew :app:installDebug
adb logcat -c
adb shell am start -W -n <package>/<activity>   # plus navigation steps to reach the screen
adb logcat -d -v threadtime -b main,crash > attempt-<n>.txt
python scripts/fingerprint.py --compare logcat.txt attempt-<n>.txt
```

Otherwise, give the user those exact commands and wait for `attempt-<n>.txt`.
The verdict table in §5.3 is then applied to the new capture. A capture
counts as valid only if it shows the target screen was reached (a
`Displayed`/`ActivityTaskManager` line or the reproduction steps the user
confirms); a clean log from a screen that was never opened is not evidence.

### 6.3 Reporting confidence

| Evidence | Allowed claim |
|---|---|
| Tier 1 + Tier 2 | "Fixed and verified on runtime." |
| Tier 1 only | "Statically consistent with the trace; pending runtime confirmation." |
| Neither | No fix may be claimed. |

## 7. Edge Case Handling

### 7.1 Ambiguous or degraded logs

| Situation | Detection | Action |
|---|---|---|
| `TRUNCATED` cause chain | Ends in `... N more` with no deepest `Caused by`, or the chain stops at `InflateException` | Diagnose from the inflating class + XML; request `adb logcat -b crash` (untruncated) and mark hypotheses `low` confidence |
| Obfuscated frames | Frames like `a.b.c(Unknown Source:4)` | Request `mapping.txt` and run `retrace`; meanwhile rely on layout name and `Error inflating class` text, which R8 does not rewrite |
| Interleaved crashes | Multiple `FATAL EXCEPTION` blocks / PIDs | Use the last one for the app package; mention earlier ones only if they share a fingerprint |
| Generic inflation message | `Error inflating class <unknown>` or only `Binary XML file line #N` | Trust the line number, not the class; resolve the tag at line `N` directly |
| `VERSION_SKEW` | Line `N` is blank, a comment, or not on a start tag | Ask whether the XML matches the build that produced the log; do not patch by proximity |
| Data Binding layouts | Root is `<layout>` | Line numbers refer to the preprocessed file; map by the inflating class and ids instead of raw line numbers |
| Nested includes | Several `in <pkg>:layout/...` references | The deepest reference is the failing file; outer ones are the include path to walk in `HIERARCHY` scope |
| Recursive include | `StackOverflowError` with repeating `LayoutInflater.parseInclude` frames | Look for a layout that includes itself directly or via a cycle |
| No exception at all | User reports a visual defect (`CL-SILENT`) | Skip fingerprinting; verification becomes the Tier-1 constraint invariants plus a user-confirmed screenshot |
| Non-layout root cause | Deepest cause is outside §1.1 | Exit `OUT_OF_SCOPE` with what was ruled out |

### 7.2 Differential diagnosis when the root cause is not apparent

When Phase 1 yields more than one plausible family, or confidence of the top
hypothesis is below `medium`:

1. List at most three competing hypotheses, each with its predicted log
   outcome.
2. Find the **discriminating probe**: the cheapest observation whose result
   differs between hypotheses — a file to request, a single attribute to
   inspect, or a one-line experiment (e.g. temporarily replacing a custom view
   tag with `View` to separate `INF-CLASS` from `INF-ATTR`).
3. Prefer asking for the probe over spending a patch iteration; a question
   does not consume an iteration, a wrong patch does.
4. If the user cannot provide the probe, proceed with the highest-ranked
   hypothesis and record the unresolved alternatives in `assumptions` so the
   next backtrack starts from them.

### 7.3 Guardrails

- Never fabricate log lines, resource names, or file contents; if something is
  needed and absent, request it.
- Never report `RESOLVED` from Tier 1 alone.
- Never stack a new guess on top of a failed patch.
- Never exceed three patch iterations; escalate with the ledger instead.

## 8. Output Contract

~~~markdown
## UI-Physician report
**Status:** RESOLVED | PENDING_RUNTIME_CONFIRMATION
**Family:** <code> — <one-line mechanism>
**Location:** <layout file>:<line> (<element>)

### Diagnosis
<cause chain, from deepest cause to the user-visible crash, 3–6 lines>

### Fix
```diff
<unified diff against the original files>
```

### Verification
- Tier 1: <checks passed>
- Tier 2: <capture used and verdict, or the commands the user must run>

### Iteration ledger
| # | Scope | Hypothesis | Verdict |
|---|---|---|---|
~~~
