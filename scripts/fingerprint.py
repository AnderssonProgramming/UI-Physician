#!/usr/bin/env python3
"""Extract a stable crash fingerprint from an Android Logcat dump.

The fingerprint identifies "the same crash" across fix attempts (SKILL.md 3.1)
and drives the verdicts of the self-correction loop (SKILL.md 5.3).

Usage:
    fingerprint.py LOG [--app-package PKG]
    fingerprint.py --compare BASELINE_LOG CANDIDATE_LOG [--app-package PKG]

Output is JSON on stdout. Only the Python standard library is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from typing import Optional

# --- Line formats -----------------------------------------------------------

THREADTIME = re.compile(
    r"^\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3}\s+(?P<pid>\d+)\s+(?P<tid>\d+)\s+"
    r"(?P<level>[VDIWEFA])\s+(?P<tag>[^:]*?)\s*:\s?(?P<msg>.*)$"
)
BRIEF = re.compile(
    r"^(?P<level>[VDIWEFA])/(?P<tag>[^(]+?)\(\s*(?P<pid>\d+)\):\s?(?P<msg>.*)$"
)
STUDIO = re.compile(
    r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3}\s+(?P<pid>\d+)-(?P<tid>\d+)\s+"
    r"(?P<tag>\S+)\s+(?P<pkg>\S+)\s+(?P<level>[VDIWEFA])\s+(?P<msg>.*)$"
)

# --- Stack trace grammar ----------------------------------------------------

EXCEPTION = re.compile(
    r"^(?:Caused by:\s+)?(?P<cls>(?:[\w$]+\.)+[\w$]*(?:Exception|Error|Throwable))"
    r"(?::\s?(?P<msg>.*))?$"
)
FRAME = re.compile(r"^at\s+(?P<method>[\w$.<>]+)\((?P<src>[^)]*)\)")
BINARY_XML = re.compile(
    r"Binary XML file line #(?P<line>\d+)(?: in (?P<pkg>[\w.]+):layout/(?P<layout>\w+))?"
)
INFLATING = re.compile(r"Error inflating class (?P<cls>[\w$.]+)")
NESTED = re.compile(r"(?:[\w$]+\.)+[\w$]*(?:Exception|Error):")
DISPLAYED = re.compile(r"Displayed (?P<component>[\w.$/]+)")

FRAMEWORK_PREFIXES = (
    "android.", "androidx.", "com.android.", "com.google.android.material.",
    "java.", "javax.", "kotlin.", "kotlinx.", "dalvik.", "libcore.", "sun.",
)


@dataclass
class Record:
    pid: Optional[str]
    tag: Optional[str]
    msg: str


@dataclass
class Exc:
    cls: str
    msg: str
    frames: list = field(default_factory=list)


@dataclass
class Fingerprint:
    root_exception: str
    root_message: str
    layout: Optional[str]
    inflating_class: Optional[str]
    app_frame: Optional[str]
    xml_line: Optional[int]
    chain: list
    truncated: bool
    id: str = ""


# --- Parsing ----------------------------------------------------------------

def parse_records(text: str) -> list[Record]:
    """Split a dump into records; unprefixed lines continue the previous one."""
    records: list[Record] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        m = THREADTIME.match(line) or STUDIO.match(line) or BRIEF.match(line)
        if m:
            records.append(Record(m.group("pid"), m.group("tag").strip(), m.group("msg")))
        elif records and records[-1].tag is not None:
            # Android Studio prints multi-line messages without a header.
            records.append(Record(records[-1].pid, records[-1].tag, line))
        else:
            records.append(Record(None, None, line))
    return records


def crash_block(records: list[Record]) -> list[str]:
    """Return the message lines of the last fatal crash, filtered to its PID."""
    starts = [i for i, r in enumerate(records) if "FATAL EXCEPTION" in r.msg]
    if not starts:
        # Raw pasted trace: start at the first line that looks like an exception.
        for i, r in enumerate(records):
            if EXCEPTION.match(r.msg.strip()):
                return [x.msg for x in records[i:]]
        return []
    start = starts[-1]
    head = records[start]
    return [
        r.msg for r in records[start + 1:]
        if r.pid == head.pid and (head.tag is None or r.tag == head.tag)
    ]


def parse_chain(lines: list[str]) -> tuple[list[Exc], bool]:
    chain: list[Exc] = []
    truncated = False
    for line in (l.strip() for l in lines):
        if line.startswith("Process:"):
            continue
        frame = FRAME.match(line)
        if frame and chain:
            chain[-1].frames.append((frame.group("method"), frame.group("src")))
            continue
        exc = EXCEPTION.match(line)
        if exc and (not chain or line.startswith("Caused by:")):
            chain.append(Exc(exc.group("cls"), exc.group("msg") or ""))
    # "... N more" only elides shared frames. The chain is truncated when its
    # deepest entry is a wrapper: an InflateException, or a message that names
    # a nested exception whose own "Caused by:" line never appears.
    if chain:
        root = chain[-1]
        truncated = root.cls.endswith("InflateException") or bool(NESTED.search(root.msg))
    return chain, truncated


def normalize(msg: str) -> str:
    msg = re.sub(r"DexPathList\[.*\]", "DexPathList[...]", msg)
    msg = re.sub(r"Binary XML file line #\d+", "Binary XML file line #?", msg)
    msg = re.sub(r"0x[0-9a-fA-F]+", "0x?", msg)
    msg = re.sub(r"@[0-9a-fA-F]{5,}\b", "@?", msg)
    return msg.strip()


def app_frame(chain: list[Exc], app_package: Optional[str]) -> Optional[str]:
    for exc in reversed(chain):  # root cause outward
        for method, _src in exc.frames:
            if app_package:
                if method.startswith(app_package + "."):
                    return method
            elif not method.startswith(FRAMEWORK_PREFIXES):
                return method
    return None


def fingerprint(text: str, app_package: Optional[str] = None) -> Optional[Fingerprint]:
    chain, truncated = parse_chain(crash_block(parse_records(text)))
    if not chain:
        return None
    root = chain[-1]

    layout = xml_line = inflating = None
    for exc in chain:  # deepest match wins
        for m in BINARY_XML.finditer(exc.msg):
            xml_line = int(m.group("line"))
            layout = m.group("layout") or layout
        m = INFLATING.search(exc.msg)
        if m:
            inflating = m.group("cls")

    fp = Fingerprint(
        root_exception=root.cls,
        root_message=normalize(root.msg),
        layout=layout,
        inflating_class=inflating,
        app_frame=app_frame(chain, app_package),
        xml_line=xml_line,
        chain=[e.cls for e in chain],
        truncated=truncated,
    )
    key = "|".join(str(v) for v in (
        fp.root_exception, fp.root_message, fp.layout, fp.inflating_class, fp.app_frame,
    ))
    fp.id = hashlib.sha1(key.encode()).hexdigest()[:12]
    return fp


# --- Verdicts (SKILL.md 5.3) --------------------------------------------------

def compare(baseline_text: str, candidate_text: str, app_package: Optional[str]) -> dict:
    base = fingerprint(baseline_text, app_package)
    cand = fingerprint(candidate_text, app_package)
    result = {
        "baseline": asdict(base) if base else None,
        "candidate": asdict(cand) if cand else None,
    }

    if base is None:
        result["verdict"] = "NO_BASELINE_CRASH"
    elif cand is None:
        displayed = [m.group("component") for m in DISPLAYED.finditer(candidate_text)]
        # A clean log only counts if the screen was actually reached (SKILL.md 6.2).
        result["verdict"] = "RESOLVED" if displayed else "UNVERIFIED"
        result["displayed"] = displayed
    elif cand.id == base.id:
        result["verdict"] = "PERSISTED"
    else:
        result["verdict"] = "MUTATED"
        result["progress_hint"] = progress_hint(base, cand)
    return result


def progress_hint(base: Fingerprint, cand: Fingerprint) -> str:
    """Heuristic input for MUTATED_PROGRESS vs MUTATED_REGRESSION.

    The final call is the agent's, because only it knows which element the
    patch touched and the inflation order across layouts.
    """
    if base.layout and cand.layout and base.layout != cand.layout:
        return "different_layout"
    if base.xml_line is not None and cand.xml_line is not None:
        if cand.xml_line > base.xml_line:
            return "later_line"
        if cand.xml_line == base.xml_line:
            return "same_element_new_failure"
        return "earlier_line"
    return "unknown"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("logs", nargs="+", metavar="LOG")
    parser.add_argument("--compare", action="store_true",
                        help="compare BASELINE_LOG against CANDIDATE_LOG")
    parser.add_argument("--app-package",
                        help="package prefix that identifies app frames")
    args = parser.parse_args(argv)

    def read(path: str) -> str:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()

    if args.compare:
        if len(args.logs) != 2:
            parser.error("--compare needs exactly two logs")
        out = compare(read(args.logs[0]), read(args.logs[1]), args.app_package)
    else:
        if len(args.logs) != 1:
            parser.error("pass one log, or use --compare with two")
        fp = fingerprint(read(args.logs[0]), args.app_package)
        out = asdict(fp) if fp else {"error": "no crash found"}

    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if "error" not in out else 1


if __name__ == "__main__":
    sys.exit(main())
