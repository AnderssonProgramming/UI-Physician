# Error Signature Catalog

Lookup table used by UI-Physician Phase 1 (classification) and Phase 2
(hypothesis generation). Match against the **deepest** `Caused by:` first; the
outer `InflateException` only tells you *where*, not *why*.

Each entry lists, in priority order, the causes to try per scope. A cause
listed under `HIERARCHY` or `ENVIRONMENT` should only be proposed after the
`LOCAL` causes have been ruled out or falsified by the loop.

Placeholders: `<X>` a class name, `<N>` an integer, `<name>` a resource name.

---

## INF-CLASS — view class cannot be loaded

```text
android.view.InflateException: Binary XML file line #<N> in <pkg>:layout/<name>: Error inflating class <X>
Caused by: java.lang.ClassNotFoundException: Didn't find class "<X>" on path: DexPathList[...]
```

| Scope | Cause | Canonical fix |
|---|---|---|
| LOCAL | Typo or stale package in the XML tag (class moved/renamed) | Set the tag to the exact FQCN from the class's `package` + name |
| LOCAL | Pre-AndroidX tag in an AndroidX project (`android.support.constraint.ConstraintLayout`) | Replace with `androidx.constraintlayout.widget.ConstraintLayout` |
| ENVIRONMENT | Library that provides `<X>` is not a dependency of the module (e.g. missing `androidx.constraintlayout:constraintlayout`, `com.google.android.material:material`) | Add the dependency to the inflating module |
| ENVIRONMENT | Class lives in a module the app module does not depend on | Add the module dependency or move the view |

Invariant: `tag == package + "." + simpleName` of a class present on the runtime classpath.

## INF-CTOR — custom view cannot be instantiated from XML

```text
Error inflating class <X>
Caused by: java.lang.NoSuchMethodException: <X>.<init> [class android.content.Context, interface android.util.AttributeSet]
```

| Scope | Cause | Canonical fix |
|---|---|---|
| LOCAL | Custom view only declares `(Context)` | Add a `(Context, AttributeSet?)` constructor, or in Kotlin `class X @JvmOverloads constructor(ctx: Context, attrs: AttributeSet? = null, defStyleAttr: Int = 0)` |
| ENVIRONMENT | R8 removed the constructor (custom keep rules override the defaults) | Keep `<init>(android.content.Context, android.util.AttributeSet)` for the class |

A `Caused by: java.lang.reflect.InvocationTargetException` instead means the
constructor **exists but threw**: continue unwinding to its own `Caused by`.

## INF-RES — resource not found for this configuration

```text
Caused by: android.content.res.Resources$NotFoundException: Resource ID #0x<hex>
Caused by: android.content.res.Resources$NotFoundException: Drawable <pkg>:drawable/<name> with resource ID #0x<hex>
```

| Scope | Cause | Canonical fix |
|---|---|---|
| LOCAL | Vector drawable set through `android:src`/`android:background` on a pre-21 device | Use `app:srcCompat` with `vectorDrawables.useSupportLibrary = true` |
| ENVIRONMENT | Resource exists only in a qualified folder (`drawable-v24`, `values-night`, `layout-land`) that the device does not match | Add a default (unqualified) variant |
| ENVIRONMENT | `Resource ID #0x0`: an attribute resolved to nothing (theme attr missing) | Treat as INF-THEME |

## INF-THEME — theme does not provide what the view needs

```text
Caused by: java.lang.UnsupportedOperationException: Failed to resolve attribute at index <N>: TypedValue{t=0x2/d=0x<hex> a=-1}
Caused by: java.lang.IllegalArgumentException: The style on this component requires your app theme to be Theme.MaterialComponents (or a descendant).
```

`t=0x2` means the value is an unresolved **theme attribute** reference
(`?attr/...`).

| Scope | Cause | Canonical fix |
|---|---|---|
| LOCAL | `?attr/<name>` used on the element is not defined by any theme in its context | Define the attr in the app theme, or reference a concrete `@color`/`@dimen` |
| HIERARCHY | An ancestor's `android:theme` overlay changes the context the view is inflated with | Fix or remove the overlay; overlays must be `ThemeOverlay.*` |
| ENVIRONMENT | Activity theme in `AndroidManifest.xml` is not a Material/AppCompat descendant | Re-parent the theme (e.g. `Theme.MaterialComponents.*.Bridge` to keep AppCompat behaviour) |
| ENVIRONMENT | Layout inflated with a non-activity context (`applicationContext`) that carries no theme | Inflate with the activity/fragment context |

## INF-ATTR — wrong attribute type or missing mandatory attribute

```text
Caused by: java.lang.UnsupportedOperationException: Can't convert value at index <N> to dimension: type=0x<hex>
Caused by: java.lang.RuntimeException: Binary XML file line #<N>: You must supply a layout_width attribute.
```

| Scope | Cause | Canonical fix |
|---|---|---|
| LOCAL | Attribute points to a resource of the wrong type (`@string` where a dimension is expected) | Point to the correct resource type |
| LOCAL | `layout_width`/`layout_height` missing on the element | Add both (`0dp` = match constraints inside ConstraintLayout) |
| HIERARCHY | Value arrives through a `style` or theme default rather than the element | Fix the style item |

## INF-MERGE — `<merge>` without a parent

```text
android.view.InflateException: Binary XML file line #<N> in <pkg>:layout/<name>: <merge /> can be used only with a valid ViewGroup root and attachToRoot=true
```

| Scope | Cause | Canonical fix |
|---|---|---|
| LOCAL | Layout rooted in `<merge>` inflated via `inflate(res, null)` / `setContentView` of an adapter item | Inflate with `inflate(res, parent, true)` (custom compound view), or replace `<merge>` with a real ViewGroup |
| HIERARCHY | `<merge>` layout used as a RecyclerView item or Fragment root | Use a real root ViewGroup |

## INF-PARENT — view attached twice

```text
java.lang.IllegalStateException: The specified child already has a parent. You must call removeView() on the child's parent first.
```

| Scope | Cause | Canonical fix |
|---|---|---|
| LOCAL | `onCreateView`/`onCreateViewHolder` inflates with `attachToRoot = true` and returns the view | Use `inflate(res, container, false)` |
| LOCAL | A cached view instance is added to a new parent | Remove from the old parent or inflate a new instance |

## INF-FRAG — static fragment inflation

```text
Error inflating class fragment
Caused by: java.lang.IllegalArgumentException: Binary XML file line #<N>: Duplicate id 0x<hex>, tag null, or parent id 0x<hex> with another fragment for <X>
```

| Scope | Cause | Canonical fix |
|---|---|---|
| LOCAL | `<fragment>` without `android:id`/`android:tag`, or a layout containing `<fragment>` inflated twice | Give it an id; migrate to `androidx.fragment.app.FragmentContainerView` |
| LOCAL | `android:name` class not found or not a `Fragment` | Correct the FQCN (then follow INF-CLASS) |

## CL-IDS — ConstraintSet needs ids on every child

```text
java.lang.RuntimeException: All children of ConstraintLayout must have ids to use ConstraintSet
    at androidx.constraintlayout.widget.ConstraintSet.clone(...)
```

Thrown from code (`ConstraintSet.clone`, `TransitionManager` + ConstraintSet
flows, MotionLayout), not from inflation, so there is no `Binary XML file`
line: locate the layout through the first app frame.

| Scope | Cause | Canonical fix |
|---|---|---|
| LOCAL | A direct child in the layout has no `android:id` | Add an id |
| HIERARCHY | `<include>` without `android:id` whose included root has no id | Add `android:id` on the `<include>` (it overrides the included root's id) |
| HIERARCHY | `<merge>` target contributes several id-less children | Add ids inside the merged layout |
| HIERARCHY | Views added programmatically without `View.generateViewId()` | Assign ids before `clone()` |

Invariant: **every direct child**, after include/merge expansion and
programmatic additions, has an id.

## CL-PARAMS — LayoutParams of the wrong parent type

```text
java.lang.ClassCastException: android.widget.<Parent>$LayoutParams cannot be cast to androidx.constraintlayout.widget.ConstraintLayout$LayoutParams
```

| Scope | Cause | Canonical fix |
|---|---|---|
| LOCAL | Code casts `view.layoutParams` to `ConstraintLayout.LayoutParams` but the view's parent is not a ConstraintLayout | Cast to the real parent's params type, or `ViewGroup.MarginLayoutParams` |
| HIERARCHY | A layout was refactored (root changed) while code still assumes the old parent | Align code with the parent in the current XML |
| LOCAL | View created in code and added with the wrong params type | Pass `ConstraintLayout.LayoutParams` to `addView` |

## CL-SILENT — constraint defects with no exception

No `FATAL EXCEPTION`. Symptoms: a view renders at the top-left (0,0), has
zero size, overlaps siblings, or ignores a chain.

| Scope | Cause | Canonical fix |
|---|---|---|
| LOCAL | Missing horizontal or vertical constraint (lint `MissingConstraints`) | Add at least one constraint on each axis |
| LOCAL | `0dp` dimension without constraints on both sides of that axis | Constrain both sides, or use `wrap_content` |
| LOCAL | Constraint references an id that is not a sibling (lives in another layout or nested ViewGroup) | Reference a sibling or `parent`; the reference is otherwise ignored |
| HIERARCHY | Circular dependency (A→B→A) or a chain whose head/tail is not anchored | Break the cycle; anchor both chain ends |
| HIERARCHY | `layout_constraint*` attributes on a view whose parent is not a ConstraintLayout | Move the view or the constraints |

Verification is purely Tier 1 (invariants) plus a user-confirmed screenshot;
there is no fingerprint to compare.
