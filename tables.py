"""Compact pipe table formatter for Subway CLI and tools."""
import datetime
import time
from typing import Any, Sequence


def format_id(val: Any) -> str:
    return str(val or "")


def truncate_text(val: Any, max_len: int = 40) -> str:
    s = str(val if val is not None else "")
    s = " ".join(s.split())
    if len(s) > max_len:
        return s[:max_len]
    return s


def format_age(created_at: Any) -> str:
    if not created_at:
        return ""
    try:
        if isinstance(created_at, (int, float)):
            delta = time.time() - created_at
        else:
            s = str(created_at).replace("Z", "+00:00")
            dt = datetime.datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            delta = datetime.datetime.now(datetime.timezone.utc).timestamp() - dt.timestamp()
        if delta < 0:
            return "0s"
        if delta < 60:
            return f"{int(delta)}s"
        if delta < 3600:
            return f"{int(delta // 60)}m"
        if delta < 86400:
            return f"{int(delta // 3600)}h"
        return f"{int(delta // 86400)}d"
    except Exception:
        return ""


def format_tokens_k(tok: Any) -> str:
    try:
        t = float(tok or 0)
        return str(round(t / 1000.0))
    except (ValueError, TypeError):
        return "0"


def table(title: str, cols: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    """Format records as a compact pipe table.

    Example:
    # workers 23 (id|prov|model|state|tok_k|age|role)
    w_675981|claude|sonnet|done|412|2h|Stats orchestrator v4
    """
    col_str = "|".join(str(c) for c in cols)
    t = str(title).strip()
    if not t.startswith("#"):
        t = f"# {t}"
    header = f"{t} ({col_str})"

    lines = [header]
    for row in rows:
        row_cells = []
        for cell in row:
            c = "" if cell is None else str(cell)
            c = c.replace("\r", " ").replace("\n", " ").strip()
            row_cells.append(c)
        line = "|".join(row_cells).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)
