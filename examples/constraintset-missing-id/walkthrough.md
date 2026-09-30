# Worked example: `CL-IDS` with a backtrack

A full UI-Physician session over the files in this directory. Iteration 1
fails for a realistic reason, and iteration 2 shows the backtrack, scope
widening and exhaustive re-scan from SKILL.md §5.4.

> Framework line numbers in the logs are illustrative. The app frames match
> `input/CheckoutFragment.kt` exactly.

| File | Role |
|---|---|
| `logs/logcat-baseline.txt` | Crash reported by the user |
| `input/fragment_checkout.xml`, `input/CheckoutFragment.kt` | Supplied at intake |
| `input/view_promo_banner.xml` | Requested during the backtrack (not supplied at intake) |
| `logs/logcat-attempt-1.txt`, `logs/logcat-attempt-2.txt` | Post-fix captures |
| `patches/attempt-1.diff`, `patches/attempt-2.diff` | Patches emitted per iteration |

---

## Phase 0: Intake

| Gate | Result |
|---|---|
| G1 fatal block | ✅ `FATAL EXCEPTION: main`, PID 18244 |
| G2 deepest cause present | ✅ single exception, no wrapper |
| G3 layout supplied | ✅ n/a for `CL-*` (thrown from code); layout found through the app frame |
| G4 obfuscation | ✅ readable frames |
| G5 version skew | ✅ `CheckoutFragment.kt:21` is `set.clone(root)` |
| G6 in scope | ✅ `CL-IDS` |

```sh
$ python scripts/fingerprint.py examples/constraintset-missing-id/logs/logcat-baseline.txt --app-package com.example.shop
```

```json
{
  "root_exception": "java.lang.RuntimeException",
  "root_message": "All children of ConstraintLayout must have ids to use ConstraintSet",
  "layout": null,
  "inflating_class": null,
  "app_frame": "com.example.shop.checkout.CheckoutFragment.applyCompactMode",
  "xml_line": null,
  "chain": [
    "java.lang.RuntimeException"
  ],
  "truncated": false,
  "id": "a0e44e951736"
}
```

`baseline.fingerprint = a0e44e951736`. Baseline XML frozen.

## Phase 1: Analysis

1. **Cause chain:** one exception, thrown by `ConstraintSet.clone`.
2. **Locate:** app frame `CheckoutFragment.applyCompactMode(CheckoutFragment.kt:21)`
   → `set.clone(root)`, where `root` is the fragment view inflated from
   `R.layout.fragment_checkout`.
3. **Classify:** `CL-IDS`. Invariant: every direct child of the
   ConstraintLayout has an id.
4. **Evidence map** (direct children of the root, as written in the file):

   | Line | Element | `android:id` |
   |---|---|---|
   | 8 | `TextView` | `checkout_title` |
   | 18 | `TextView` | `checkout_subtitle` |
   | 28 | `LinearLayout` | `subtotal_row` |
   | 50 | `View` (divider) | **none** |
   | 59 | `<include layout="@layout/view_promo_banner">` | `promo_banner` |
   | 63 | `MaterialButton` | `pay_button` |

## Iteration 1: scope `LOCAL`

| Field | Value |
|---|---|
| Hypothesis H1 | The divider `View` at line 50 is the only child without an id |
| Evidence | Log: `All children of ConstraintLayout must have ids`; XML: `fragment_checkout.xml:50` |
| Assumption A1 | The `<include>` at line 59 contributes one child, and `android:id="@+id/promo_banner"` applies to it |
| Predicted outcome | `RESOLVED` |

Tier 1 passes **under A1**: every direct child in the file now has an id.

```diff
--- a/res/layout/fragment_checkout.xml
+++ b/res/layout/fragment_checkout.xml
@@ -48,6 +48,7 @@
     </LinearLayout>
 
     <View
+        android:id="@+id/subtotal_divider"
         android:layout_width="0dp"
         android:layout_height="1dp"
         android:layout_marginTop="12dp"
```

### Verification

```sh
$ python scripts/fingerprint.py --compare logs/logcat-baseline.txt logs/logcat-attempt-1.txt --app-package com.example.shop
"verdict": "PERSISTED"      # candidate id a0e44e951736 == baseline id
```

**Verdict: `PERSISTED`.** H1 is falsified *as a complete explanation*. The
ledger records:

```yaml
- iteration: 1
  scope: LOCAL
  hypothesis: "divider View at fragment_checkout.xml:50 is the only id-less child"
  assumptions: [A1]
  patch: patches/attempt-1.diff
  observed_fingerprint: a0e44e951736
  verdict: PERSISTED
  rejected_because: "same fingerprint after every in-file child had an id; A1 unverified"
```

## Backtrack (§5.4.1)

1. **Revert** `attempt-1.diff`. The next patch is derived from the baseline.
2. **Invalidate A1** and turn it into an evidence request. This costs no
   iteration:
   > Please share `res/layout/view_promo_banner.xml`. The crash persists even
   > though every child written in `fragment_checkout.xml` would have an id, so
   > the included layout is the next suspect.
3. **Widen scope** `LOCAL → HIERARCHY`.
4. **Re-scan exhaustively**, with includes expanded, against the `CL-IDS`
   invariant.

The requested file arrives. Its root is `<merge>`, so the evidence map
changes:

| Source | Direct child after inflation | Effective id |
|---|---|---|
| `fragment_checkout.xml:8` | `TextView` | `checkout_title` |
| `fragment_checkout.xml:18` | `TextView` | `checkout_subtitle` |
| `fragment_checkout.xml:28` | `LinearLayout` | `subtotal_row` |
| `fragment_checkout.xml:50` | `View` | **none** |
| `view_promo_banner.xml:7` (via merge) | `ImageView` | `promo_icon` |
| `view_promo_banner.xml:17` (via merge) | `TextView` | **none** |
| `fragment_checkout.xml:63` | `MaterialButton` | `pay_button` |

**Key finding:** when an `<include>` targets a `<merge>` layout,
`LayoutInflater` adds the merged children straight to the parent and **ignores
the `android:id` (and any `layout_*` overrides) on the `<include>` tag**. A1
was false. The include contributes two children, one of which has no id.

## Iteration 2: scope `HIERARCHY`

| Field | Value |
|---|---|
| Hypothesis H2 | Two effective children lack ids: the divider (`fragment_checkout.xml:50`) and the merged promo `TextView` (`view_promo_banner.xml:17`) |
| Evidence | Log token as above; `<merge>` root in `view_promo_banner.xml:2`; exhaustive child table |
| Assumptions | none |
| Predicted outcome | `RESOLVED` |

The exhaustive re-scan re-derives the divider fix as well. Attempt 1 was
incomplete, not wrong, so reverting and re-deriving produces one complete
patch instead of a stack of partial guesses.

```diff
--- a/res/layout/fragment_checkout.xml
+++ b/res/layout/fragment_checkout.xml
@@ -48,6 +48,7 @@
     </LinearLayout>
 
     <View
+        android:id="@+id/subtotal_divider"
         android:layout_width="0dp"
         android:layout_height="1dp"
         android:layout_marginTop="12dp"
--- a/res/layout/view_promo_banner.xml
+++ b/res/layout/view_promo_banner.xml
@@ -15,6 +15,7 @@
         app:layout_constraintStart_toStartOf="parent" />
 
     <TextView
+        android:id="@+id/promo_text"
         android:layout_width="0dp"
         android:layout_height="wrap_content"
         android:layout_marginStart="8dp"
```

Tier 1: coverage ✅, no new resource references ✅, `CL-IDS` invariant over
all 7 effective children ✅, blast radius: two attribute additions, no ids
removed ✅.

### Verification

```sh
$ python scripts/fingerprint.py --compare logs/logcat-baseline.txt logs/logcat-attempt-2.txt
"verdict": "RESOLVED",
"displayed": ["com.example.shop/.checkout.CheckoutActivity"]
```

No fatal block for the app, and the `Displayed` line shows the screen was
reached. Tier 2 is satisfied.

## Final report

~~~markdown
## UI-Physician report
**Status:** RESOLVED
**Family:** CL-IDS: ConstraintSet.clone() requires an id on every direct child
**Location:** fragment_checkout.xml:50 (View) and view_promo_banner.xml:17 (TextView, merged)

### Diagnosis
ConstraintSet.clone() in CheckoutFragment.applyCompactMode (CheckoutFragment.kt:21)
throws when any direct child of the root ConstraintLayout has no id. Two such
children exist after inflation: the subtotal divider, and the promo TextView
that view_promo_banner.xml injects through <merge>. The android:id on the
<include> tag is ignored for <merge> targets, so it did not cover them.

### Fix
patches/attempt-2.diff

### Verification
- Tier 1: coverage, resolution, CL-IDS invariant (7/7 children), blast radius
- Tier 2: logs/logcat-attempt-2.txt, no fatal block, CheckoutActivity displayed

### Iteration ledger
| # | Scope | Hypothesis | Verdict |
|---|---|---|---|
| 1 | LOCAL | Divider View is the only id-less child | PERSISTED |
| 2 | HIERARCHY | Divider + merged promo TextView lack ids | RESOLVED |

**Note:** `android:id="@+id/promo_banner"` on the `<include>` has no effect
and is misleading. Removing it is outside this fix's blast radius, so it is
left for the team to decide.
~~~
