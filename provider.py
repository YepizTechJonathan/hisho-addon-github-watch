"""GitHub watch for Hisho (see addon.yaml). Runs inside Core. Reads one repository's pull requests and
Actions runs with GET calls only (Core's `gh` login, or a read-only token) and turns what changed into short
lines: PR opened / merged / closed, CI results, deploy workflow results. Each line goes to its channel through
the add-on that provides ``chat_messages`` (Hisho's Discord add-on), but only when the entry says ``post: true``;
otherwise it is journaled and listed on the page as "would post", so the wording can be checked first.

Nothing here writes to GitHub: no comments, reviews, approvals, merges or re-runs, and it never looks past
GitHub (a deploy's cloud account is not touched; only GitHub's record of the run is read).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

PAGE = 30            # newest PRs and runs read each check
KEEP = 400           # ids remembered per kind
RECENT = 15          # events kept for the page


class WatchError(RuntimeError):
    pass


# ── GitHub, GET only ──────────────────────────────────────────────────────

def fetch(entry: dict[str, Any], ctx: Any, path: str) -> Any:
    """``GET /repos/<repo><path>``. With ``token`` a read-only token is used, else Core's `gh` login."""
    url_path = f"/repos/{entry['repo']}{path}"
    if entry.get("token"):
        req = urllib.request.Request("https://api.github.com" + url_path, method="GET",
                                     headers={"Authorization": f"Bearer {ctx.secret(entry['token'])}",
                                              "Accept": "application/vnd.github+json", "User-Agent": "hisho-github-watch"})
        try:
            with urllib.request.urlopen(req, timeout=20) as f:
                return json.loads(f.read())
        except urllib.error.HTTPError as exc:
            raise WatchError(f"GitHub answered {exc.code} on {path or '/'}") from None
        except (urllib.error.URLError, OSError) as exc:
            raise WatchError(f"GitHub not reachable: {getattr(exc, 'reason', exc)}") from None
    try:
        res = ctx.run(["gh", "api", "--method", "GET", url_path.lstrip("/")], timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WatchError(f"gh failed: {exc}") from None
    if res.returncode:
        raise WatchError(f"gh api {path or '/'}: {(res.stderr or res.stdout).strip()[-200:]}")
    return json.loads(res.stdout or "null")


def _when(s: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def _mins(run: dict[str, Any]) -> str:
    a, b = _when(run.get("run_started_at")), _when(run.get("updated_at"))
    return f"{max(1, round((b - a).total_seconds() / 60))} min" if a and b else ""


def deploy_map(entry: dict[str, Any]) -> dict[str, str]:
    """``Deploy Dev=dev-testing, Deploy Prod=prod-releases`` -> {workflow name: channel}."""
    out = {}
    for part in str(entry.get("deploys") or "").split(","):
        name, _, ch = part.partition("=")
        if name.strip() and ch.strip():
            out[name.strip()] = ch.strip()
    return out


def failed_step(entry: dict[str, Any], ctx: Any, run_id: int) -> str:
    try:
        for job in (fetch(entry, ctx, f"/actions/runs/{run_id}/jobs") or {}).get("jobs") or []:
            if job.get("conclusion") == "failure":
                step = next((s["name"] for s in job.get("steps") or [] if s.get("conclusion") == "failure"), "")
                return f"{job['name']}" + (f" › {step}" if step else "")
    except WatchError:
        pass
    return ""


# ── what changed ─────────────────────────────────────────────────────────

def _state_file(name: str) -> Path:
    base = Path(os.environ.get("HISHO_STATE_DIR") or "~/.local/state/hisho").expanduser()
    return base / "addons" / "github-watch" / f"{name}.json"


def _pr_state(pr: dict[str, Any]) -> str:
    return "merged" if pr.get("merged_at") else str(pr.get("state"))


def events(entry: dict[str, Any], ctx: Any, prs: list[dict[str, Any]], runs: list[dict[str, Any]],
           before: dict[str, Any]) -> list[dict[str, Any]]:
    """[{channel, text, kind}] for what is new since ``before`` (PR states by number, finished run ids)."""
    out: list[dict[str, Any]] = []
    pr_ch, alerts = entry.get("pr_channel"), entry.get("alerts_channel")
    ci, deploys = str(entry.get("ci_workflow") or "CI"), deploy_map(entry)
    seen_prs, seen_runs = before.get("prs") or {}, set(before.get("runs") or [])
    by_branch = {pr["head"]["ref"]: pr for pr in prs if pr.get("state") == "open"}

    def add(channel: Any, text: str, kind: str) -> None:
        if channel:
            out.append({"channel": str(channel), "text": text, "kind": kind})

    for pr in sorted(prs, key=lambda p: p["number"]):
        num, now, was = pr["number"], _pr_state(pr), seen_prs.get(str(pr["number"]))
        title, link, base = pr["title"], pr["html_url"], pr["base"]["ref"]
        if was is None and now == "open":
            add(pr_ch, f"🔀 PR #{num} opened by {pr['user']['login']} → {base}: \"{title}\" · {link}", "pr")
        elif was in ("open", None) and now == "merged":   # None: opened and merged between two checks
            who = ""
            try:
                who = (fetch(entry, ctx, f"/pulls/{num}").get("merged_by") or {}).get("login") or ""
            except WatchError:
                pass
            add(pr_ch, f"✅ PR #{num} merged{' by ' + who if who else ''} into {base}: \"{title}\" · {link}", "pr")
        elif was == "open" and now == "closed":
            add(pr_ch, f"✖️ PR #{num} closed without merging: \"{title}\" · {link}", "pr")
    for run in sorted(runs, key=lambda r: r["id"]):
        if run.get("status") != "completed" or run["id"] in seen_runs:
            continue
        ok, name, link = run.get("conclusion") == "success", run.get("name"), run.get("html_url")
        if run.get("conclusion") in ("cancelled", "skipped", "neutral"):
            continue
        sha = str(run.get("head_sha") or "")[:7]
        msg = str((run.get("head_commit") or {}).get("message") or "").split("\n")[0][:80]
        if name == ci:
            pr = next(iter(run.get("pull_requests") or []), None) or by_branch.get(run.get("head_branch"))
            where = f"PR #{pr['number']}" if pr else f"{run.get('head_branch')} @ {sha}"
            if ok and pr:
                add(pr_ch, f"🧪 CI ✅ passed for {where} (run #{run['run_number']}"
                                  f"{', ' + _mins(run) if _mins(run) else ''}) · {link}", "ci")
            elif not ok:
                step = failed_step(entry, ctx, run["id"])
                add(alerts or pr_ch, f"❌ CI failed for {where}{' · ' + step if step else ''} · {link}", "ci-failed")
        elif name in deploys:
            if ok:
                quoted = f' "{msg}"' if msg else ""
                add(deploys[name], f"🚀 {name} #{run['run_number']} succeeded: {run.get('head_branch')} @ {sha}"
                                   f"{quoted}{' (' + _mins(run) + ')' if _mins(run) else ''} · {link}", "deploy")
            else:
                step = failed_step(entry, ctx, run["id"])
                text = (f"❌ {name} #{run['run_number']} failed on {run.get('head_branch')} @ {sha}"
                        f"{' · ' + step if step else ''} · {link}")
                add(deploys[name], text, "deploy-failed")
                if alerts and alerts != deploys[name]:
                    add(alerts, text, "deploy-failed")
    return out


def remember(prs: list[dict[str, Any]], runs: list[dict[str, Any]], before: dict[str, Any]) -> dict[str, Any]:
    prs_state = {**(before.get("prs") or {}), **{str(p["number"]): _pr_state(p) for p in prs}}
    done = list(before.get("runs") or []) + [r["id"] for r in runs if r.get("status") == "completed"
                                             and r["id"] not in set(before.get("runs") or [])]
    keep = sorted(prs_state, key=int)[-KEEP:]
    return {"prs": {k: prs_state[k] for k in keep}, "runs": done[-KEEP:], "recent": before.get("recent") or []}


def post_chat(entry: dict[str, Any], channel: str, text: str) -> dict[str, Any]:
    from hisho import addons
    addon, module, ents = addons.capability("chat_messages")
    return module.post_message(ents, addons.Context(addon), channel, text, entry.get("chat_server") or None)


def check(name: str, entry: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """Read, compare with the last check, post (or only list) the new lines. A failed post keeps the old
    state so the next check sends it again (lines already sent in that check are not repeated)."""
    prs = fetch(entry, ctx, f"/pulls?state=all&sort=updated&direction=desc&per_page={PAGE}") or []
    runs = (fetch(entry, ctx, f"/actions/runs?per_page={PAGE}") or {}).get("workflow_runs") or []
    path = _state_file(name)
    try:
        before = json.loads(path.read_text())
    except (OSError, ValueError):
        before = None
    if before is None:   # the first look only remembers
        state, new, failed = remember(prs, runs, {}), [], ""
    else:
        new, failed = events(entry, ctx, prs, runs, before), ""
        sent = set(before.get("sent_pending") or [])
        for ev in new:
            key = f"{ev['channel']}|{ev['text']}"
            ev["posted"] = False
            if entry.get("post") is True and key not in sent and not failed:
                res = post_chat(entry, ev["channel"], ev["text"])
                ev["posted"] = bool(res.get("ok"))
                if ev["posted"]:
                    sent.add(key)
                else:
                    failed = f"#{ev['channel']}: {res.get('error')}"
        if failed:   # keep the old state; remember what already went out so it isn't sent twice
            state = {**before, "sent_pending": sorted(sent)}
        else:
            state = remember(prs, runs, before)
        if new:
            state["recent"] = ([{"at": datetime.now().astimezone().isoformat(timespec="minutes"), **ev}
                                for ev in new] + list(before.get("recent") or []))[:RECENT]
            try:
                from hisho.jobs import append_event
                append_event(Path(os.environ.get("HISHO_ROOT") or "~/Hisho").expanduser(), "github-watch",
                             {"addon": "github-watch", "entry": name, "repo": entry["repo"],
                              "lines": [f"#{e['channel']} {e['text']}" for e in new],
                              "posted": entry.get("post") is True and not failed})
            except Exception:  # noqa: BLE001
                pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1))
    return {"prs": prs, "runs": runs, "new": new, "failed": failed, "recent": state.get("recent") or []}


# ── what Core calls ───────────────────────────────────────────────────────

def _last(runs: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((r for r in runs if r.get("name") == name and r.get("status") == "completed"), None)


def _mark(run: dict[str, Any] | None) -> str:
    return "never run" if not run else "✅" if run.get("conclusion") == "success" else f"❌ {run.get('conclusion')}"


def summary(entries: dict[str, dict[str, Any]], ctx: Any) -> dict[str, Any]:
    if not entries:
        return {"state": "unknown", "headline": "No repository watched yet", "problems": []}
    problems, heads = [], []
    for name, entry in entries.items():
        try:
            c = check(name, entry, ctx)
        except Exception as exc:  # noqa: BLE001
            problems.append({"entry": name, "severity": "warn", "key": "github", "title": f"{name}: can't read GitHub",
                             "detail": f"{entry.get('repo')}: {str(exc)[:200]}"})
            continue
        if c["failed"]:
            problems.append({"entry": name, "severity": "warn", "key": "post", "title": f"{name}: post failed",
                             "detail": c["failed"][:200] + ". Hisho tries again next check."})
        open_prs = [p for p in c["prs"] if p.get("state") == "open"]
        bits = [f"{len(open_prs)} open PR" + ("s" if len(open_prs) != 1 else "")]
        bits += [f"{w} {_mark(_last(c['runs'], w))}" for w in [entry.get("ci_workflow") or "CI", *deploy_map(entry)]]
        heads.append(f"{name}: " + " · ".join(bits) + ("" if entry.get("post") is True else " · not posting"))
    return {"state": "warn" if problems else "ok", "headline": " · ".join(heads) or "not answering",
            "problems": problems}


def details(entries: dict[str, dict[str, Any]], ctx: Any) -> list[dict[str, Any]]:
    sections = []
    for name, entry in entries.items():
        try:
            prs = fetch(entry, ctx, f"/pulls?state=open&per_page={PAGE}") or []
            runs = (fetch(entry, ctx, f"/actions/runs?per_page={PAGE}") or {}).get("workflow_runs") or []
        except WatchError as exc:
            sections.append({"title": name, "entry": name, "state": "down", "facts": [str(exc)], "items": []})
            continue
        try:
            recent = json.loads(_state_file(name).read_text()).get("recent") or []
        except (OSError, ValueError):
            recent = []
        posting = entry.get("post") is True
        mapping = [f"PRs and CI → #{entry['pr_channel']}" if entry.get("pr_channel") else "PRs: not posted"]
        mapping += [f"{w} → #{ch}" for w, ch in deploy_map(entry).items()]
        mapping += [f"failures → #{entry['alerts_channel']}"] if entry.get("alerts_channel") else []
        facts = [f"{entry['repo']} · read only (GET) · " + ("posting" if posting else "NOT posting: lines are only listed"),
                 "Channels: " + "; ".join(mapping)]
        facts += [("Posted" if e.get("posted") else "Would post") + f" {e['at']} in #{e['channel']}: {e['text']}"
                  for e in recent[:8]]
        if entry.get("note"):
            facts.append(entry["note"])
        items = [{"id": f"pr-{p['number']}", "name": f"PR #{p['number']} {p['title']}", "url": p["html_url"],
                  "state": "up", "facts": [f"{p['user']['login']} · {p['head']['ref']} → {p['base']['ref']}"
                                           + (" · draft" if p.get("draft") else "")], "actions": []} for p in prs]
        for w in [entry.get("ci_workflow") or "CI", *deploy_map(entry)]:
            r = _last(runs, w)
            items.append({"id": f"wf-{re.sub(r'[^A-Za-z0-9]+', '-', w).strip('-').lower()}", "name": w,
                          "url": (r or {}).get("html_url"), "state": "off" if not r else
                          "up" if r.get("conclusion") == "success" else "warn",
                          "facts": [_mark(r) + (f" · #{r['run_number']} {r.get('head_branch')} @ "
                                                f"{str(r.get('head_sha'))[:7]} · {str(r.get('updated_at'))[:16]}" if r else "")],
                          "actions": []})
        sections.append({"title": entry["repo"], "entry": name, "state": "ok", "facts": facts, "items": items})
    return sections


def test(entry: dict[str, Any], ctx: Any) -> dict[str, Any]:
    try:
        repo = fetch(entry, ctx, "")
    except WatchError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "message": f"Can read {repo['full_name']} ({repo.get('visibility')}, default branch "
                                   f"{repo.get('default_branch')})"}
