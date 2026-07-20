"""Shared CLI output: a pretty human mode and a stable machine (TSV) mode.

Two `rich` consoles — `out` (stdout, data) and `err` (stderr, status) — plus one
table renderer (`render_table`) that is the single choke point for tabular
output. Each command builds plain string rows; those rows *are* the machine
output (one tab-joined line each), so the two modes can't drift. Human mode wraps
the same rows in a `rich.table.Table` and decorates them; it never becomes a
second data path.

Mode resolution (high → low precedence):

    --plain flag  >  KARAKUM_OUTPUT=plain|rich env  >  auto (stdout is a TTY)

Status helpers (`warn/error/info/done`, plus `detail` for indented continuation
lines) keep the `karakum:` prefix and print to stderr. On a non-TTY (a pipe, or
pytest's capsys / click's CliRunner) they emit their text verbatim — no color, no
wrapping — so callers and tests can rely on the exact wording; color only lands
on a real terminal.
"""
import os
import sys

from rich import box
from rich.console import Console
from rich.prompt import Confirm
from rich.table import Table
from rich.text import Text

# `highlight=False` stops rich from auto-styling numbers / paths / quotes inside
# our own status text, which keeps substrings byte-stable. `file` is left as the
# default so each console resolves the *current* sys.stdout/sys.stderr lazily —
# capsys / CliRunner swap those in, and we still write to the captured stream.
out = Console(highlight=False)
err = Console(stderr=True, highlight=False)


def _rich_mode(plain):
    """Resolve output mode: --plain flag > KARAKUM_OUTPUT env > stdout-is-a-TTY."""
    if plain is not None:
        return not plain
    env = os.environ.get("KARAKUM_OUTPUT")
    if env in ("plain", "rich"):
        return env == "rich"
    return out.is_terminal


# --- status (stderr) -------------------------------------------------------

def _status(msg, style=None):
    # On a real terminal, render styled + un-wrapped via rich; everywhere else
    # (pipes, capsys, CliRunner) fall back to a verbatim print so the exact text
    # survives for downstream consumers and tests.
    if err.is_terminal and style:
        err.print(Text(f"karakum: {msg}", style=style), soft_wrap=True)
    else:
        print(f"karakum: {msg}", file=sys.stderr)


def info(msg):
    """Neutral status line (`karakum: …`)."""
    _status(msg)


def done(msg):
    """Success confirmation (green on a terminal)."""
    _status(msg, "green")


def warn(msg):
    """Warning / soft failure (yellow on a terminal)."""
    _status(msg, "yellow")


def error(msg):
    """Hard error, usually paired with a non-zero exit (red on a terminal)."""
    _status(msg, "bold red")


def detail(msg):
    """Indented continuation line under a status message (no `karakum:` prefix)."""
    if err.is_terminal:
        err.print(Text(f"        {msg}", style="dim"), soft_wrap=True)
    else:
        print(f"        {msg}", file=sys.stderr)


def confirm(question, *, default=False):
    """Yes/no prompt rendered on stderr so piped stdout stays clean."""
    return Confirm.ask(question, default=default, console=err)


# --- tables ----------------------------------------------------------------

def render_table(columns, rows, *, styles=None, plain=None):
    """Render tabular data in the resolved mode from one set of plain rows.

    `columns` is the header list; `rows` is a sequence of equal-length string
    tuples. In machine mode each row is printed verbatim as a tab-joined line
    (headerless, byte-stable — this is the source of truth). In human mode the
    same rows become a rich table; `styles` maps a column name to either a static
    rich style or a callable `cell -> style|None` for per-value coloring, and is
    ignored entirely in machine mode.
    """
    if not _rich_mode(plain):
        for row in rows:
            print("\t".join(row))
        return

    styles = styles or {}
    table = Table(box=box.SIMPLE, header_style="bold", pad_edge=False)
    for col in columns:
        table.add_column(col)
    for row in rows:
        cells = []
        for col, value in zip(columns, row):
            style = styles.get(col)
            if callable(style):
                style = style(value)
            cells.append(Text(value, style=style or ""))
        table.add_row(*cells)
    out.print(table)
