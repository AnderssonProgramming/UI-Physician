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

