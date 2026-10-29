"""Run with Hisho's venv at the repository's top: ~/code/hisho/.venv/bin/python -m pytest -q

FakeGitHub answers the few GETs the add-on makes with the shapes GitHub's REST API returned on 2026-10-21;
a stand-in chat add-on records what would reach the team chat."""
import json
import subprocess
from pathlib import Path

import pytest

from hisho import addon_store, addons

HERE = Path(__file__).resolve().parents[1]
R = "https://github.com/acme/app"


def pr(n, state="open", merged=None, head="feature/x", base="dev", user="jane", title="Do a thing"):
    return {"number": n, "state": state, "merged_at": merged, "title": title, "html_url": f"{R}/pull/{n}",
            "user": {"login": user}, "head": {"ref": head}, "base": {"ref": base}, "draft": False}


def run(i, name, conclusion="success", branch="dev", prs=(), status="completed", n=None):
    return {"id": i, "name": name, "status": status, "conclusion": conclusion, "head_branch": branch,
            "head_sha": f"abc{i}def0", "run_number": n or i, "html_url": f"{R}/actions/runs/{i}",
            "run_started_at": "2026-10-21T10:00:00Z", "updated_at": "2026-10-21T10:04:00Z",
            "head_commit": {"message": f"Merge pull request #{i}\n\nbody"},
            "pull_requests": [{"number": p} for p in prs]}


class FakeGitHub:
    def __init__(self):
        self.prs = [pr(12, "closed", "2026-09-04T00:09:39Z", base="master", user="boss")]
        self.runs = [run(100, "CI"), run(101, "Deploy Dev")]
        self.paths = []

    def __call__(self, entry, ctx, path):
        self.paths.append(path)
        if path.startswith("/pulls?"):
            return [p for p in self.prs if "state=open" not in path or p["state"] == "open"]
        if path.startswith("/actions/runs?"):
            return {"workflow_runs": sorted(self.runs, key=lambda r: -r["id"])}
        if path.startswith("/pulls/"):
            return {"merged_by": {"login": "boss"}}
        if path.endswith("/jobs"):
            return {"jobs": [{"name": "deploy", "conclusion": "failure",
                              "steps": [{"name": "checkout", "conclusion": "success"},
                                        {"name": "sam deploy", "conclusion": "failure"}]}]}
        if path == "":
            return {"full_name": entry["repo"], "visibility": "private", "default_branch": "dev"}
        raise AssertionError(path)


ENTRY = {"app": {"repo": "acme/app", "pr_channel": "pull-requests", "alerts_channel": "alerts",
                 "deploys": "Deploy Dev=dev-testing, Deploy Prod=prod-releases"}}
POSTING = {"app": {**ENTRY["app"], "post": True}}


@pytest.fixture
def mod(addon):
    return addons.provider(addon)


@pytest.fixture
def gh(mod, monkeypatch, tmp_path):
    f = FakeGitHub()
    monkeypatch.setattr(mod, "fetch", f)
    monkeypatch.setenv("HISHO_STATE_DIR", str(tmp_path / "state"))
    return f


@pytest.fixture
def chat(stand_in):
    a = stand_in("chatx", kind="integration", provides=["chat_messages"], entry_schema={"token": "secret"})
    (a.path / "provider.py").write_text((a.path / "provider.py").read_text() + (
        "\nSENT, FAIL = [], []\n\ndef post_message(entries, ctx, channel, text, entry=None):\n"
        "    if FAIL:\n        return {'ok': False, 'error': FAIL[0]}\n"
        "    SENT.append((channel, text))\n    return {'ok': True, 'id': 'm'}\n"))
    addons.set_entry(a, "team", {"token": "HISHO_T"})
    addons.enable("chatx")
    return addons.provider(addons.get("chatx"))


def happen(gh):
    """A day on the repository: a PR opened, CI on it, a merge, a dev deploy, a failed deploy, a failed CI."""
    gh.prs.append(pr(13, title="fix(audio): race"))
    gh.prs[0:1] = [pr(11, "closed", "2026-10-21T09:00:00Z")]                    # was open before, merged now
    gh.runs += [run(102, "CI", prs=[13], branch="feature/x"), run(103, "Deploy Dev"),
                run(104, "Deploy Dev", "failure", n=7), run(105, "CI", "failure", branch="feature/x"),
                run(106, "CI", "cancelled"), run(107, "Deploy Dev", None, status="in_progress")]


def test_manifest_is_valid_and_readme_matches(addon):
    assert addons.validate_manifest(addon.manifest) == []
    assert addon_store.readme_problems(HERE) == []


def test_no_entries_is_unknown_not_an_alert(addon, ctx_for):
    assert addons.provider(addon).summary({}, ctx_for(addon))["state"] == "unknown"


def test_first_look_only_remembers_then_lists_without_posting(mod, gh, addon, ctx_for, chat):
    ctx = ctx_for(addon)
    first = mod.check("app", ENTRY["app"], ctx)
    assert first["new"] == []
    state = json.loads(mod._state_file("app").read_text())
    state["prs"]["11"] = "open"
    mod._state_file("app").write_text(json.dumps(state))
    happen(gh)
    c = mod.check("app", ENTRY["app"], ctx)
    lines = [(e["channel"], e["text"]) for e in c["new"]]
    assert lines == [
        ("pull-requests", "✅ PR #11 merged by boss into dev: \"Do a thing\" · https://github.com/acme/app/pull/11"),
        ("pull-requests", "🔀 PR #13 opened by jane → dev: \"fix(audio): race\" · https://github.com/acme/app/pull/13"),
        ("pull-requests", "🧪 CI ✅ passed for PR #13 (run #102, 4 min) · https://github.com/acme/app/actions/runs/102"),
        ("dev-testing", "🚀 Deploy Dev #103 succeeded: dev @ abc103d \"Merge pull request #103\" (4 min) · "
                        "https://github.com/acme/app/actions/runs/103"),
        ("dev-testing", "❌ Deploy Dev #7 failed on dev @ abc104d · deploy › sam deploy · https://github.com/acme/app/actions/runs/104"),
        ("alerts", "❌ Deploy Dev #7 failed on dev @ abc104d · deploy › sam deploy · https://github.com/acme/app/actions/runs/104"),
        ("alerts", "❌ CI failed for PR #13 · deploy › sam deploy · https://github.com/acme/app/actions/runs/105"),
    ]
    assert chat.SENT == []                                         # post is off by default
    sec = mod.details(ENTRY, ctx)[0]
    assert sec["facts"][0] == "acme/app · read only (GET) · NOT posting: lines are only listed"
    assert sec["facts"][1] == ("Channels: PRs and CI → #pull-requests; Deploy Dev → #dev-testing; "
                               "Deploy Prod → #prod-releases; failures → #alerts")
    assert sec["facts"][2].startswith("Would post ") and "in #pull-requests: ✅ PR #11 merged" in sec["facts"][2]
    assert mod.check("app", ENTRY["app"], ctx)["new"] == []        # never twice
    gh.prs.append(pr(14, "closed", "2026-10-21T11:00:00Z", title="quick fix"))   # opened and merged between checks
    gh.prs.append(pr(15, "closed", None, title="abandoned"))                      # opened and closed: not worth a post
    assert [e["text"][:22] for e in mod.check("app", ENTRY["app"], ctx)["new"]] == ["✅ PR #14 merged by bos"]
    names = {it["name"]: it for it in sec["items"]}
    assert names["Deploy Prod"]["facts"] == ["never run"] and names["Deploy Dev"]["state"] == "warn"


def test_posting_and_a_failed_post_is_retried_without_repeats(mod, gh, addon, ctx_for, chat):
    ctx = ctx_for(addon)
    mod.check("app", POSTING["app"], ctx)
    gh.prs.append(pr(13))
    gh.runs.append(run(103, "Deploy Dev"))
    chat.FAIL.append("the bot can't post in #dev-testing")
    s = mod.summary(POSTING, ctx)
    assert [p["title"] for p in s["problems"]] == ["app: post failed"]
    assert chat.SENT == []
    chat.FAIL.clear()
    assert mod.summary(POSTING, ctx)["problems"] == []
    assert [c for c, _ in chat.SENT] == ["pull-requests", "dev-testing"]
    mod.summary(POSTING, ctx)
    assert len(chat.SENT) == 2


def test_summary_headline(mod, gh, addon, ctx_for, chat):
    gh.prs.append(pr(13))
    assert mod.summary(ENTRY, ctx_for(addon))["headline"] == \
        "app: 1 open PR · CI ✅ · Deploy Dev ✅ · Deploy Prod never run · not posting"


def test_github_unreadable_is_a_warning(mod, gh, addon, ctx_for, monkeypatch):
    def broken(*a):
        raise mod.WatchError("gh api /pulls: HTTP 404")
    monkeypatch.setattr(mod, "fetch", broken)
    monkeypatch.setattr(mod, "signed_in", lambda ctx: True)   # never the real gh here
    s = mod.summary(ENTRY, ctx_for(addon))
    assert [p["title"] for p in s["problems"]] == ["app: can't read GitHub"]
    assert mod.test(ENTRY["app"], ctx_for(addon))["ok"] is False


def test_github_is_only_ever_read(addon, ctx_for, monkeypatch):
    m = addons.provider(addon)
    calls = []

    class Ctx:
        def run(self, cmd, timeout=30):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"full_name": "acme/app"}), "")
    assert m.test({"repo": "acme/app"}, Ctx())["ok"]
    assert calls == [["gh", "api", "--method", "GET", "repos/acme/app"]]
    src = (HERE / "provider.py").read_text()
    for verb in ("POST", "PATCH", "PUT", "DELETE"):
        assert f'"{verb}"' not in src and f"'{verb}'" not in src



class GhCtx:
    """This machine's `gh`, stood in for: what each command prints and its exit code."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def run(self, cmd, timeout=30):
        self.calls.append(cmd)
        out, rc = self.answers.get(" ".join(cmd), ("", 1))
        return subprocess.CompletedProcess(cmd, rc, out, "")


REPOS = "gh api --method GET user/repos?per_page=100&sort=pushed"
SIGNED_IN = {"gh auth status --hostname github.com": ("", 0)}


def test_found_lists_your_repositories_not_watched_yet(mod):
    ctx = GhCtx({**SIGNED_IN, REPOS: (json.dumps([{"full_name": "acme/app"}, {"full_name": "acme/site"},
                                     {"full_name": "acme/old", "archived": True}, {"full_name": "bad name"}]), 0)})
    got = mod.discover(ENTRY, ctx)
    assert got == [{"title": "acme/site", "name": "site", "fill": {"repo": "acme/site"}}]
    assert ctx.calls[1:] == [REPOS.split(" ")]                    # one GET after the sign-in check, nothing else


@pytest.mark.parametrize("answer", [("", 1), ("not json", 0), ("{}", 0)])
def test_found_is_quiet_when_github_doesnt_answer(mod, answer):
    assert mod.discover({}, GhCtx({**SIGNED_IN, REPOS: answer})) == []


def test_without_a_sign_in_the_page_offers_githubs_sign_in_never_a_network_search(mod, addon):
    got = mod.discover({}, GhCtx({}))                               # gh auth status fails: not signed in
    assert got["items"] == [] and got["need"]["button"] == "github-signin"
    assert not addon.manifest.get("discover")                       # no network probes at all
    assert addon.manifest["find"]["title"] == "Your GitHub repositories"


def test_no_token_and_no_sign_in_says_what_to_do(mod):
    ctx = GhCtx({"gh auth token": ("", 1)})                     # gh api fails too (no answer given)
    got = mod.test({"repo": "acme/app"}, ctx)
    assert not got["ok"] and "isn't signed in to GitHub" in got["explain"]
    ok = GhCtx({"gh api --method GET repos/acme/app": (json.dumps({"full_name": "acme/app"}), 0)})
    assert "this machine's GitHub sign-in" in mod.test({"repo": "acme/app"}, ok)["message"]


def test_the_page_offers_found_and_the_token_may_be_pasted(addon):
    from hisho import connect
    assert connect.view(addon)["discover"] is True
    assert addon.manifest["entry_schema"]["token"] == "password?"
