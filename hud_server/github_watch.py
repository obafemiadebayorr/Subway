"""GitHub mail trigger: reads the repo through the user's GitHub MCP sign-in (HUD MCP list, via mcp_hub).

One source, any OS. Each check is one API call on the condition's own interval (default 60 s, minimum 30 s);
GitHub cannot push events to a laptop without a public relay.
"""
import json
import re
import subprocess
from typing import Any, Dict, List, Tuple


class GhMissing(RuntimeError):
    """GitHub is not connected (name kept for callers)."""


NOT_CONNECTED = "Connect GitHub in the HUD's MCP list (MCP servers → github → Sign in)"
EVENTS = ("push", "pr_opened", "pr_merged", "ci", "issue", "comment", "release", "tag", "branch", "review")
NEEDS_NUMBER = ("comment", "review")  # GitHub's MCP reads these per issue/PR


def _call(tool: str, args: Dict[str, Any]) -> Any:
    import mcp_hub
    try:
        res = mcp_hub.hub_call("github", tool, args, timeout=30)
    except Exception as e:
        msg = str(e)
        if "401" in msg or "403" in msg or "not found" in msg.lower() or "unknown server" in msg.lower():
            raise GhMissing(NOT_CONNECTED) from e
        raise
    if isinstance(res, str) and ("401" in res or "sign in" in res.lower()) and not res.lstrip().startswith(("[", "{")):
        raise GhMissing(NOT_CONNECTED)
    return _parse(res)


def _parse(res: Any) -> Any:
    """MCP tool result -> JSON value (list or dict)."""
    if isinstance(res, dict) and isinstance(res.get("content"), list):
        if res.get("isError"):
            text = " ".join(str(c.get("text") or "") for c in res["content"] if isinstance(c, dict))
            raise RuntimeError(text[:200] or "GitHub MCP error")
        for c in res["content"]:
            if isinstance(c, dict) and c.get("type") == "text":
                try:
                    return json.loads(c.get("text") or "")
                except Exception:
                    continue
        return None
    if isinstance(res, str):
        try:
            return json.loads(res)
        except Exception:
            return None
    return res


def _items(val: Any) -> List[Dict[str, Any]]:
    """The record list inside a result, whatever key GitHub's MCP wraps it in."""
    if isinstance(val, list):
        return [x for x in val if isinstance(x, dict)]
    if isinstance(val, dict):
        for k in ("items", "issues", "pull_requests", "pullRequests", "check_runs", "checkRuns", "reviews", "comments", "nodes"):
            if isinstance(val.get(k), list):
                return [x for x in val[k] if isinstance(x, dict)]
        for v in val.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
        return [val] if val else []
    return []


def _g(d: Dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if d.get(k) not in (None, ""):
            return d.get(k)
    return None


def _int(v: Any) -> int:
    try:
        return int(v or 0)
    except Exception:
        return 0


def default_repo(project_dir: str) -> str:
    """owner/name from the project's git remote (github.com only); "" when unknown."""
    try:
        out = subprocess.run(["git", "remote", "get-url", "origin"], cwd=project_dir or None,
                             capture_output=True, text=True, timeout=10)
    except Exception:
        return ""
    m = re.search(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", (out.stdout or "").strip())
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def _owner_repo(cond: Dict[str, Any]) -> Dict[str, str]:
    owner, _, repo = str(cond.get("repo") or "").partition("/")
    if not owner or not repo:
        raise ValueError("github condition requires 'repo' as owner/name")
    return {"owner": owner, "repo": repo}


def _newest_open_pr(base: Dict[str, str]) -> int:
    prs = _items(_call("list_pull_requests", {**base, "state": "open", "sort": "updated", "direction": "desc", "perPage": 1}))
    return _int(prs[0].get("number")) if prs else 0


def _read(cond: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """(marker, newest record) for cond['event']."""
    ev, base, branch = cond.get("event"), _owner_repo(cond), str(cond.get("branch") or "")
    if ev in NEEDS_NUMBER and not _int(cond.get("number")):
        raise ValueError(f"github '{ev}' needs 'number' (the issue or PR to watch)")
    if ev == "push":
        rows = _items(_call("list_commits", {**base, "perPage": 1, **({"sha": branch} if branch else {})}))
        top = rows[0] if rows else {}
        return {"sha": str(top.get("sha") or "")}, top
    if ev == "pr_opened":
        rows = _items(_call("list_pull_requests", {**base, "state": "open", "sort": "created", "direction": "desc", "perPage": 5}))
        top = max(rows, key=lambda r: _int(r.get("number")), default={})
        return {"number": _int(top.get("number"))}, top
    if ev == "pr_merged":
        rows = _items(_call("list_pull_requests", {**base, "state": "closed", "sort": "updated", "direction": "desc", "perPage": 10}))
        merged = [r for r in rows if _g(r, "merged_at", "mergedAt")]
        top = max(merged, key=lambda r: str(_g(r, "merged_at", "mergedAt"))) if merged else {}
        return {"merged_at": str(_g(top, "merged_at", "mergedAt") or "")}, top
    if ev == "issue":
        rows = _items(_call("list_issues", {**base, "state": "OPEN", "orderBy": "CREATED_AT", "direction": "DESC", "perPage": 5}))
        rows = [r for r in rows if not r.get("pull_request")]
        top = max(rows, key=lambda r: _int(r.get("number")), default={})
        return {"number": _int(top.get("number"))}, top
    if ev == "comment":
        rows = _items(_call("issue_read", {**base, "method": "get_comments", "issue_number": _int(cond["number"]), "perPage": 100}))
        top = max(rows, key=lambda r: _int(r.get("id")), default={})
        return {"id": _int(top.get("id"))}, top
    if ev == "review":
        rows = _items(_call("pull_request_read", {**base, "method": "get_reviews", "pullNumber": _int(cond["number"]), "perPage": 100}))
        want = str(cond.get("state") or "any").upper()
        rows = [r for r in rows if want == "ANY" or str(r.get("state") or "").upper() == want]
        top = max(rows, key=lambda r: _int(r.get("id")), default={})
        return {"id": _int(top.get("id"))}, top
    if ev == "ci":
        pr = _int(cond.get("number")) or _newest_open_pr(base)
        if not pr:
            return {"run_id": 0}, {}
        rows = _items(_call("pull_request_read", {**base, "method": "get_check_runs", "pullNumber": pr, "perPage": 100}))
        want = str(cond.get("conclusion") or "any").lower()
        done = [r for r in rows if str(r.get("status") or "").lower() == "completed"
                and (want == "any" or str(r.get("conclusion") or "").lower() == want)]
        top = max(done, key=lambda r: _int(r.get("id")), default={})
        return {"run_id": _int(top.get("id"))}, {**top, "_pr": pr}
    if ev == "release":
        top = _call("get_latest_release", base)
        top = top if isinstance(top, dict) else {}
        return {"tag": str(top.get("tag_name") or "")}, top
    if ev in ("tag", "branch"):
        rows = _items(_call("list_tags" if ev == "tag" else "list_branches", {**base, "perPage": 100}))
        names = sorted({str(r.get("name") or "") for r in rows} - {""})
        return {"names": names}, {}
    raise ValueError(f"unknown github event '{ev}' (one of: {', '.join(EVENTS)})")


def snapshot(cond: Dict[str, Any]) -> Dict[str, Any]:
    """Current marker for cond['event'], stored as the baseline when the trigger is created."""
    return _read(cond)[0]


def check(cond: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    """(fired, reason, detail): compares the live marker with cond['baseline']."""
    ev, repo = cond.get("event"), cond.get("repo")
    base = cond.get("baseline") or {}
    mark, top = _read(cond)
    quiet = (False, f"No new {ev} on {repo}", {})
    if ev == "push":
        if mark["sha"] and mark["sha"] != base.get("sha"):
            msg = str(((top.get("commit") or {}).get("message") or "")).split("\n")[0]
            br = f"@{cond['branch']}" if cond.get("branch") else ""
            return True, f"New push to {repo}{br}: {mark['sha'][:7]} {msg}".rstrip(), mark
        return quiet
    if ev in ("pr_opened", "issue"):
        if mark["number"] > _int(base.get("number")):
            kind = "PR" if ev == "pr_opened" else "Issue"
            return True, f"{kind} #{mark['number']} opened: {top.get('title') or ''}".rstrip(), mark
        return quiet
    if ev == "pr_merged":
        if mark["merged_at"] and mark["merged_at"] > str(base.get("merged_at") or ""):
            return True, f"PR #{_int(top.get('number'))} merged: {top.get('title') or ''}".rstrip(), mark
        return quiet
    if ev in ("comment", "review"):
        if mark["id"] > _int(base.get("id")):
            who = ((top.get("user") or top.get("author") or {}) or {}).get("login") or ""
            if ev == "review":
                return True, f"Review on #{cond['number']}: {top.get('state') or ''} {who}".strip(), mark
            return True, f"New comment on #{cond['number']} by {who}: {str(top.get('body') or '')[:80]}", mark
        return quiet
    if ev == "ci":
        if mark["run_id"] > _int(base.get("run_id")):
            return True, f"CI {top.get('name') or ''} {top.get('conclusion') or ''} on PR #{top.get('_pr')}".replace("  ", " "), mark
        return quiet
    if ev == "release":
        if mark["tag"] and mark["tag"] != base.get("tag"):
            return True, f"New release {mark['tag']} on {repo}: {top.get('name') or ''}".rstrip(), mark
        return quiet
    if ev in ("tag", "branch"):
        new = sorted(set(mark["names"]) - set(base.get("names") or []))
        if new:
            return True, f"New {ev} on {repo}: {', '.join(new[:5])}", mark
        return quiet
    return quiet
