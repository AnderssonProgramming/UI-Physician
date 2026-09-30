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

