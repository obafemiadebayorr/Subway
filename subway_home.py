"""Where Subway keeps its state: ~/.subway (it was ~/.subway-context before the rename).

subway_dir() is the one place that decides the folder. Until `subway install` moves the old
folder, a machine that only has ~/.subway-context keeps using it, so nothing splits in two.
"""
import os

NEW_NAME = ".subway"
OLD_NAME = ".subway-context"


def subway_dir(home=None):
    """SUBWAY_DIR when set; otherwise ~/.subway, or ~/.subway-context while only that real folder exists."""
    if home is None:
        env = os.environ.get("SUBWAY_DIR")
        if env:
            return env
        home = os.path.expanduser("~")
    new, old = os.path.join(home, NEW_NAME), os.path.join(home, OLD_NAME)
    if not os.path.lexists(new) and os.path.isdir(old) and not os.path.islink(old):
        return old
    return new


def migrate(home=None):
    """Move ~/.subway-context to ~/.subway once and leave a link at the old path, so old paths
    (worktrees, shell hooks, running processes) keep working. Returns a line for the user, or ""."""
    if home is None and os.environ.get("SUBWAY_DIR"):
        return ""
    home = home or os.path.expanduser("~")
    new, old = os.path.join(home, NEW_NAME), os.path.join(home, OLD_NAME)
    if os.path.islink(old) or not os.path.isdir(old):
        return ""
    if os.path.lexists(new):
        return f"Both {old} and {new} exist, so neither was moved. Merge them by hand, then remove {old}."
    try:
        os.rename(old, new)
    except OSError as e:
        return f"Could not move {old} to {new}: {e}"
    try:
        os.symlink(new, old)
    except OSError as e:
        return f"Moved {old} to {new}, but could not leave a link at the old path: {e}"
    return f"Moved {old} to {new}; the old path now links to it."
