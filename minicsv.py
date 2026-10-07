#!/usr/bin/env python3
"""
minicsv.py — a small, dependency-free CSV reader + CLI.

Handles:
  * quoted fields, embedded delimiters and newlines
  * escaped quotes ("")
  * LF, CRLF and lone CR line endings
  * streaming reads (chunked, so large files don't blow up memory)
  * headers, ragged rows, optional type inference

Usage:
  python minicsv.py data.csv
  python minicsv.py data.csv --json --infer-types
  cat data.csv | python minicsv.py -d ';' --no-header
"""

from __future__ import annotations

import argparse
import io
import itertools
import json
import re
import sys
from typing import Any, Iterable, Iterator, Sequence, TextIO

CHUNK = 1 << 16  # 64 KiB


class CSVError(ValueError):
    """Raised when the input can't be parsed as CSV."""


# --------------------------------------------------------------------------- #
# Core parser
# --------------------------------------------------------------------------- #
def iter_rows(
    stream: TextIO,
    delimiter: str = ",",
    quotechar: str = '"',
    strict: bool = False,
) -> Iterator[list[str]]:
    """Yield rows (lists of raw string fields) from an open text stream."""
    if len(delimiter) != 1:
        raise ValueError("delimiter must be exactly one character")
    if len(quotechar) != 1:
        raise ValueError("quotechar must be exactly one character")

    field: list[str] = []
    row: list[str] = []
    in_quotes = False
    after_quote = False   # just saw a closing quote; may be a doubled quote
    started = False       # current field has begun (even if empty, e.g. "")
    skip_lf = False       # swallow the \n of a \r\n pair

    while True:
        chunk = stream.read(CHUNK)
        if not chunk:
            break

        for ch in chunk:
            if skip_lf:
                skip_lf = False
                if ch == "\n":
                    continue

            if in_quotes:
                if ch == quotechar:
                    in_quotes = False
                    after_quote = True
                else:
                    field.append(ch)
                continue

            if after_quote:
                after_quote = False
                if ch == quotechar:          # "" -> a literal quote
                    field.append(quotechar)
                    in_quotes = True
                    continue
                # otherwise fall through: delimiter / newline / stray char

            if ch == delimiter:
                row.append("".join(field))
                field.clear()
                started = False

            elif ch == "\n" or ch == "\r":
                if row or field or started:  # skip truly blank lines
                    row.append("".join(field))
                    field.clear()
                    started = False
                    yield row
                    row = []
                if ch == "\r":
                    skip_lf = True

            elif ch == quotechar and not started:
                in_quotes = True
                started = True

            else:
                field.append(ch)
                started = True

    if in_quotes and strict:
        raise CSVError("unterminated quoted field at end of input")

    if row or field or started:
        row.append("".join(field))
        yield row


def read_rows(path: str | "os.PathLike[str]", encoding: str = "utf-8-sig", **kw: Any) -> list[list[str]]:
    """Read an entire CSV file into a list of rows."""
    with open(path, "r", encoding=encoding, newline="") as fh:
        return list(iter_rows(fh, **kw))


# --------------------------------------------------------------------------- #
# Higher-level helpers
# --------------------------------------------------------------------------- #
def _dedupe(header: Sequence[str]) -> list[str]:
    """Make duplicate column names unique: a, a -> a, a_2"""
    seen: dict[str, int] = {}
    out = []
    for name in header:
        seen[name] = seen.get(name, 0) + 1
        out.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
    return out


def rows_to_dicts(
    rows: Iterable[Sequence[str]],
    header: Sequence[str] | None = None,
    restkey: str | None = None,
    restval: Any = None,
) -> Iterator[dict[str, Any]]:
    """Turn rows into dicts. If `header` is None, the first row is the header."""
    it = iter(rows)
    if header is None:
        header = next(it, None)  # type: ignore[arg-type]
        if header is None:
            return
    keys = _dedupe(list(header))

    for row in it:
        record = {k: (row[i] if i < len(row) else restval) for i, k in enumerate(keys)}
        if restkey is not None and len(row) > len(keys):
            record[restkey] = list(row[len(keys):])
        yield record


_INT_RE = re.compile(r"^[+-]?(?:0|[1-9]\d*)$")            # no leading zeros ("007" stays a str)
_FLOAT_RE = re.compile(r"^[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?$")


def infer(value: str) -> Any:
    """Best-effort scalar conversion. Returns the original string if unsure."""
    s = value.strip()
    if s == "":
        return None
    low = s.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if _INT_RE.match(s):
        return int(s)
    if _FLOAT_RE.match(s):
        return float(s)
    return value


def format_table(rows: Sequence[Sequence[Any]], header: bool = True, gap: str = "  ") -> str:
    """Render rows as a fixed-width plain-text table."""
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    widths = [
        max((len(str(r[i])) for r in rows if i < len(r)), default=0)
        for i in range(width)
    ]

    lines: list[str] = []
    for n, row in enumerate(rows):
        cells = [
            str(row[i]).ljust(widths[i]) if i < len(row) else " " * widths[i]
            for i in range(width)
        ]
        lines.append(gap.join(cells).rstrip())
        if header and n == 0:
            lines.append(gap.join("-" * w for w in widths))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read a CSV file and print it as a table or JSON.")
    ap.add_argument("path", nargs="?", default="-", help="CSV file (default: stdin)")
    ap.add_argument("-d", "--delimiter", default=",", help="field delimiter (default: ,)")
    ap.add_argument("-q", "--quotechar", default='"', help="quote character (default: \")")
    ap.add_argument("--encoding", default="utf-8-sig", help="input encoding (default: utf-8-sig)")
    ap.add_argument("--no-header", action="store_true", help="treat the first row as data")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    ap.add_argument("--infer-types", action="store_true", help="convert ints/floats/bools")
    ap.add_argument("-n", "--limit", type=int, default=None, help="only read the first N rows")
    ap.add_argument("--strict", action="store_true", help="fail on unterminated quotes")
    args = ap.parse_args(argv)

    if args.path == "-":
        stream: TextIO = io.TextIOWrapper(sys.stdin.buffer, encoding=args.encoding, newline="")
        closer = None
    else:
        stream = open(args.path, "r", encoding=args.encoding, newline="")
        closer = stream

    try:
        rows: list[list[Any]] = list(
            itertools.islice(
                iter_rows(stream, args.delimiter, args.quotechar, args.strict),
                args.limit,
            )
        )
    except (CSVError, UnicodeDecodeError) as exc:
        ap.error(str(exc))
    finally:
        if closer is not None:
            closer.close()

    if args.infer_types:
        rows = [[infer(cell) for cell in row] for row in rows]

    if args.json:
        payload: Any = rows if args.no_header else list(rows_to_dicts(rows))
        json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        print(format_table(rows, header=not args.no_header))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())