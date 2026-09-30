import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import fingerprint as fpmod  # noqa: E402

EXAMPLE_LOGS = ROOT / "examples" / "constraintset-missing-id" / "logs"

THEME_CRASH = """\
09-30 14:12:01.100  4321  4321 I ActivityManager: Start proc 12345:com.example.shop/u0a231
09-30 14:12:01.123 12345 12345 E AndroidRuntime: FATAL EXCEPTION: main
09-30 14:12:01.123 12345 12345 E AndroidRuntime: Process: com.example.shop, PID: 12345
09-30 14:12:01.123 12345 12345 E AndroidRuntime: java.lang.RuntimeException: Unable to start activity ComponentInfo{com.example.shop/com.example.shop.ProfileActivity}: android.view.InflateException: Binary XML file line #{line} in com.example.shop:layout/activity_profile: Binary XML file line #{line} in com.example.shop:layout/activity_profile: Error inflating class com.google.android.material.chip.Chip
09-30 14:12:01.123 12345 12345 E AndroidRuntime: \tat android.app.ActivityThread.performLaunchActivity(ActivityThread.java:3782)
09-30 14:12:01.123 12345 12345 E AndroidRuntime: Caused by: android.view.InflateException: Binary XML file line #{line} in com.example.shop:layout/activity_profile: Error inflating class com.google.android.material.chip.Chip
09-30 14:12:01.123 12345 12345 E AndroidRuntime: Caused by: java.lang.IllegalArgumentException: The style on this component requires your app theme to be Theme.MaterialComponents (or a descendant).
09-30 14:12:01.123 12345 12345 E AndroidRuntime: \tat com.google.android.material.internal.ThemeEnforcement.checkTheme(ThemeEnforcement.java:247)
09-30 14:12:01.124 12345 12345 E AndroidRuntime: \tat com.example.shop.ProfileActivity.onCreate(ProfileActivity.kt:{src_line})
09-30 14:12:01.124 12345 12345 E AndroidRuntime: \t... 12 more
09-30 14:12:01.130 12345 12399 W OtherTag: unrelated line from another thread
"""


def theme_crash(line=34, src_line=22):
    return THEME_CRASH.replace("{line}", str(line)).replace("{src_line}", str(src_line))


class ParseFormatsTest(unittest.TestCase):
    def test_threadtime_extracts_deepest_cause_and_location(self):
        fp = fpmod.fingerprint(theme_crash())
        self.assertEqual(fp.root_exception, "java.lang.IllegalArgumentException")
        self.assertEqual(fp.layout, "activity_profile")
        self.assertEqual(fp.xml_line, 34)
        self.assertEqual(fp.inflating_class, "com.google.android.material.chip.Chip")
        self.assertEqual(fp.app_frame, "com.example.shop.ProfileActivity.onCreate")
        self.assertEqual(len(fp.chain), 3)
        self.assertFalse(fp.truncated, "'... N more' elides frames, not causes")

    def test_brief_format(self):
        log = (
            "E/AndroidRuntime(12345): FATAL EXCEPTION: main\n"
            "E/AndroidRuntime(12345): java.lang.RuntimeException: All children of "
            "ConstraintLayout must have ids to use ConstraintSet\n"
            "E/AndroidRuntime(12345): \tat androidx.constraintlayout.widget.ConstraintSet.clone(ConstraintSet.java:3563)\n"
            "E/AndroidRuntime(12345): \tat com.example.shop.checkout.CheckoutFragment.applyCompactMode(CheckoutFragment.kt:21)\n"
        )
        fp = fpmod.fingerprint(log)
        self.assertEqual(fp.root_exception, "java.lang.RuntimeException")
        self.assertEqual(fp.app_frame, "com.example.shop.checkout.CheckoutFragment.applyCompactMode")

    def test_android_studio_format_with_unprefixed_continuations(self):
        log = (
            "2026-09-30 14:12:01.123 12345-12345 AndroidRuntime          com.example.shop                     E  FATAL EXCEPTION: main\n"
            "Process: com.example.shop, PID: 12345\n"
            "java.lang.ClassCastException: android.widget.FrameLayout$LayoutParams cannot be cast to "
            "androidx.constraintlayout.widget.ConstraintLayout$LayoutParams\n"
            "\tat com.example.shop.home.BannerBinder.bind(BannerBinder.kt:40)\n"
        )
        fp = fpmod.fingerprint(log)
        self.assertEqual(fp.root_exception, "java.lang.ClassCastException")
        self.assertEqual(fp.app_frame, "com.example.shop.home.BannerBinder.bind")

    def test_raw_trace_without_logcat_prefix(self):
        log = (
            "android.view.InflateException: Binary XML file line #12 in com.example:layout/row: "
            "Error inflating class com.example.PriceTag\n"
            "Caused by: java.lang.ClassNotFoundException: Didn't find class \"com.example.PriceTag\" "
            "on path: DexPathList[[zip file \"/data/app/base.apk\"],nativeLibraryDirectories=[/system/lib64]]\n"
        )
        fp = fpmod.fingerprint(log)
        self.assertEqual(fp.root_exception, "java.lang.ClassNotFoundException")
        self.assertIn("DexPathList[...]", fp.root_message)
        self.assertEqual(fp.layout, "row")

    def test_only_last_fatal_block_is_used(self):
        older = theme_crash().replace("IllegalArgumentException: The style", "IllegalStateException: The style")
        fp = fpmod.fingerprint(older + theme_crash())
        self.assertEqual(fp.root_exception, "java.lang.IllegalArgumentException")

    def test_no_crash_returns_none(self):
        self.assertIsNone(fpmod.fingerprint(EXAMPLE_LOGS.joinpath("logcat-attempt-2.txt").read_text()))


class TruncationTest(unittest.TestCase):
    def test_chain_ending_in_inflate_exception_is_truncated(self):
        log = (
            "android.view.InflateException: Binary XML file line #5 in com.example:layout/main: "
            "Error inflating class <unknown>\n"
        )
        self.assertTrue(fpmod.fingerprint(log).truncated)

    def test_wrapper_naming_missing_cause_is_truncated(self):
        log = (
            "java.lang.RuntimeException: Unable to start activity ComponentInfo{a/b}: "
            "java.lang.IllegalStateException: boom\n"
        )
        self.assertTrue(fpmod.fingerprint(log).truncated)


class IdentityTest(unittest.TestCase):
    def test_line_shifts_do_not_change_identity(self):
        a = fpmod.fingerprint(theme_crash(line=34, src_line=22))
        b = fpmod.fingerprint(theme_crash(line=35, src_line=23))
        self.assertEqual(a.id, b.id)
        self.assertNotEqual(a.xml_line, b.xml_line)

    def test_resource_ids_and_hashes_are_normalized(self):
        self.assertEqual(
            fpmod.normalize("Resource ID #0x7f08006b in View@1a2b3c4d"),
            "Resource ID #0x? in View@?",
        )


class CompareTest(unittest.TestCase):
    def read(self, name):
        return EXAMPLE_LOGS.joinpath(name).read_text()

    def test_example_attempt_1_persisted(self):
        result = fpmod.compare(self.read("logcat-baseline.txt"), self.read("logcat-attempt-1.txt"), "com.example.shop")
        self.assertEqual(result["verdict"], "PERSISTED")

    def test_example_attempt_2_resolved(self):
        result = fpmod.compare(self.read("logcat-baseline.txt"), self.read("logcat-attempt-2.txt"), None)
        self.assertEqual(result["verdict"], "RESOLVED")
        self.assertEqual(result["displayed"], ["com.example.shop/.checkout.CheckoutActivity"])

    def test_clean_log_without_displayed_screen_is_unverified(self):
        clean = "09-30 14:26:10.330 18802 18802 I com.example.shop: Late-enabling -Xcheck:jni\n"
        result = fpmod.compare(theme_crash(), clean, None)
        self.assertEqual(result["verdict"], "UNVERIFIED")

    def test_downstream_failure_is_mutated_with_later_line_hint(self):
        later = (
            theme_crash(line=52)
            .replace("com.google.android.material.chip.Chip", "com.example.shop.widget.PriceTag")
            .replace(
                "java.lang.IllegalArgumentException: The style on this component requires your app theme "
                "to be Theme.MaterialComponents (or a descendant).",
                "java.lang.NoSuchMethodException: com.example.shop.widget.PriceTag.<init> "
                "[class android.content.Context, interface android.util.AttributeSet]",
            )
        )
        result = fpmod.compare(theme_crash(line=34), later, None)
        self.assertEqual(result["verdict"], "MUTATED")
        self.assertEqual(result["progress_hint"], "later_line")


class CliTest(unittest.TestCase):
    def test_compare_requires_two_logs(self):
        with self.assertRaises(SystemExit):
            fpmod.main(["--compare", str(EXAMPLE_LOGS / "logcat-baseline.txt")])


if __name__ == "__main__":
    unittest.main()
