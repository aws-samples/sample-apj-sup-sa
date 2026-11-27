# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""terraform/helpers/acgw.py: Mantle projects. One data source lists once for every user and creates the missing ones
(r90: replaces the per-user processes and their shared cache, lock and override rules); a destroy archives each live
project of the user and confirms it by reading that project."""
import importlib.util
import io
import json
import os
import subprocess  # nosec B404 - runs this repo's own helper with a fixed argument list
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "terraform" / "helpers" / "acgw.py"


PYTHON = sys.executable  # the interpreter running this file; children run it too


def run_py(args, env, **kw):
    """Run a helper script with this interpreter, under the given (minimal) environment. "python3" is argv[0]; this
    interpreter's directory leads the child's PATH, so the name resolves to it and its site-packages (boto3)."""
    env = {**env, "PATH": os.path.dirname(sys.executable) + os.pathsep + env.get("PATH", "")}
    return subprocess.run(["python3", "-B", *args], executable=PYTHON, env=env, **kw)  # nosec B603 - no shell; this interpreter runs repo helpers with test-chosen args


@pytest.fixture()
def acgw(monkeypatch):
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("OCGW_USER", raising=False)
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    spec = importlib.util.spec_from_file_location("acgw_projects_under_test", str(SRC))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    # r107: stub credentials, so tests that reach the real mantle() do not depend on the machine's AWS sign-in.
    import botocore.credentials
    monkeypatch.setattr(mod.S, "get_credentials", lambda: botocore.credentials.Credentials("AKIDEXAMPLE", "x"))
    return mod


def proj(pid, name, status="active", created_at=1, tags=None):
    """A listed project; tagged as the user's project of this repo (as ensure() below names the user by the project
    name) unless tags are given."""
    return {"id": pid, "arn": f"arn:{pid}", "name": name, "status": status, "created_at": created_at,
            "tags": {"Project": "opencode-agentcore-gateway", "User": name} if tags is None else tags}


class Mantle:
    """A fake Projects API: GET list / GET one / POST create / POST archive, with hooks for each."""

    def __init__(self, mod, projects=()):
        self.mod = mod
        self.projects = [dict(p) for p in projects]
        self.calls = []
        self.on_create = None
        self.on_archive = None
        self.get_one = True

    def __call__(self, method, path, body=None, idempotent=True, empty_ok=False):
        path = path.split("?")[0]
        self.calls.append((method, path))
        parts = path.strip("/").split("/")  # v1 organization projects [id] [archive]
        if method == "GET" and len(parts) == 3:
            return {"data": [dict(p) for p in self.projects], "has_more": False}
        if method == "GET":
            if not self.get_one:
                raise self.err(405)
            found = next((p for p in self.projects if p["id"] == parts[3]), None)
            if found is None:
                raise self.err(404)
            return dict(found)
        if len(parts) == 4:  # POST /projects/{id}: merge the tags, a null removes a key (verified live, r94)
            found = next(p for p in self.projects if p["id"] == parts[3])
            tags = dict(found.get("tags") or {})
            for k, v in body["tags"].items():
                if v is None:
                    tags.pop(k, None)
                else:
                    tags[k] = v
            found["tags"] = tags
            self.sent = body["tags"]
            return dict(found)
        if parts[-1] == "archive":
            if self.on_archive:
                return self.on_archive(parts[3])
            next(p for p in self.projects if p["id"] == parts[3])["status"] = "archived"
            return {}
        if self.on_create:
            return self.on_create(body)
        p = proj(f"p-{len(self.projects) + 1}", body["name"], created_at=100 + len(self.projects))
        self.projects.append(p)
        return dict(p)

    def err(self, status):
        return self.mod.MantleError(status, f"HTTP {status}")


def ensure(acgw, monkeypatch, capsys, fake, users):
    monkeypatch.setattr(acgw, "mantle", fake)
    acgw.projects_ensure(json.dumps({u: {"User": u} for u in users}))
    return {k: json.loads(v) for k, v in json.loads(capsys.readouterr().out).items()}


def test_one_listing_serves_every_user_and_only_missing_projects_are_created(acgw, monkeypatch, capsys):
    fake = Mantle(acgw, [proj("p-a", "ocgw-alice"), proj("p-b", "ocgw-bob")])
    out = ensure(acgw, monkeypatch, capsys, fake, ["ocgw-alice", "ocgw-bob", "ocgw-carol"])
    assert [c for c in fake.calls if c[0] == "GET"] == [("GET", "/v1/organization/projects")]  # listed once
    assert [c for c in fake.calls if c == ("POST", "/v1/organization/projects")] == [("POST", "/v1/organization/projects")]  # carol only
    assert out["ocgw-alice"] == {"id": "p-a", "arn": "arn:p-a", "name": "ocgw-alice"} and out["ocgw-carol"]["name"] == "ocgw-carol"


def test_no_users_lists_nothing_to_create(acgw, monkeypatch, capsys):
    fake = Mantle(acgw)
    assert ensure(acgw, monkeypatch, capsys, fake, []) == {}


def test_with_duplicates_every_run_uses_the_oldest(acgw, monkeypatch, capsys):
    fake = Mantle(acgw, [proj("p-new", "ocgw-alice", created_at=9), proj("p-old", "ocgw-alice", created_at=1),
                   proj("p-odd", "ocgw-alice", created_at="x")])
    assert ensure(acgw, monkeypatch, capsys, fake, ["ocgw-alice"])["ocgw-alice"]["id"] == "p-old"


@pytest.mark.parametrize("status,reused", [("active", True), ("creating", True), ("pending", True), (None, True),
                                           ("archived", False), ("failed", False), ("ARCHIVED", False)])
def test_a_project_coming_up_is_the_users_and_a_dead_one_is_not(acgw, monkeypatch, capsys, status, reused):
    # r89/r90: a project still coming up (whatever the status says, or none) is never a reason to create another;
    # an archived or failed one is.
    p = proj("p-a", "ocgw-alice", status=status)
    fake = Mantle(acgw, [p])
    out = ensure(acgw, monkeypatch, capsys, fake, ["ocgw-alice"])
    assert (out["ocgw-alice"]["id"] == "p-a") is reused


def test_entries_without_an_id_name_or_arn_are_ignored(acgw, monkeypatch, capsys):
    fake = Mantle(acgw, [{"id": "p-x", "name": "ocgw-alice", "status": "active"}])
    out = ensure(acgw, monkeypatch, capsys, fake, ["ocgw-alice"])
    assert out["ocgw-alice"]["id"] != "p-x"


def test_an_unclear_create_found_in_a_later_listing_is_not_sent_again(acgw, monkeypatch, capsys):
    fake = Mantle(acgw)
    posts = []

    def lost(body):  # the project is made, but the reply never comes back
        posts.append(1)
        fake.projects.append(proj("p-a", body["name"], status="creating"))
        raise acgw.UnclearWrite("reply lost")
    fake.on_create = lost
    assert ensure(acgw, monkeypatch, capsys, fake, ["ocgw-alice"])["ocgw-alice"]["id"] == "p-a" and posts == [1]


def test_an_unclear_create_not_listed_after_a_minute_is_sent_again(acgw, monkeypatch, capsys):
    fake = Mantle(acgw)
    posts = []

    def flaky(body):
        posts.append(1)
        if len(posts) == 1:
            raise acgw.UnclearWrite("503")
        p = proj("p-a", body["name"])
        fake.projects.append(p)
        return dict(p)
    fake.on_create = flaky
    assert ensure(acgw, monkeypatch, capsys, fake, ["ocgw-alice"])["ocgw-alice"]["id"] == "p-a" and posts == [1, 1]
    assert len([c for c in fake.calls if c == ("GET", "/v1/organization/projects")]) == 1 + acgw.UNCLEAR_LOOKS


def test_a_create_reply_without_name_or_arn_is_unclear(acgw, monkeypatch, capsys):
    fake = Mantle(acgw)

    def bare(body):
        fake.projects.append(proj("p-a", body["name"]))
        return {"id": "p-a"}
    fake.on_create = bare
    assert ensure(acgw, monkeypatch, capsys, fake, ["ocgw-alice"])["ocgw-alice"] == {"id": "p-a", "arn": "arn:p-a", "name": "ocgw-alice"}


def test_unclear_creates_that_never_show_stop_with_a_message(acgw, monkeypatch, capsys):
    fake = Mantle(acgw)
    fake.on_create = lambda body: (_ for _ in ()).throw(acgw.UnclearWrite("503"))
    monkeypatch.setattr(acgw, "mantle", fake)
    with pytest.raises(acgw.HelperError, match="it may exist: run terraform plan again"):
        acgw.projects_ensure(json.dumps({"ocgw-alice": {}}))


def test_archive_archives_every_live_duplicate_and_confirms_each_by_reading_it(acgw, monkeypatch):
    fake = Mantle(acgw, [proj("p-1", "ocgw-alice"), proj("p-2", "ocgw-alice", status="creating"), proj("p-3", "ocgw-alice", status="archived")])
    monkeypatch.setattr(acgw, "mantle", fake)
    acgw.project_archive("ocgw-alice")
    assert ("POST", "/v1/organization/projects/p-1/archive") in fake.calls and ("POST", "/v1/organization/projects/p-2/archive") in fake.calls
    assert ("POST", "/v1/organization/projects/p-3/archive") not in fake.calls
    assert [c for c in fake.calls if c == ("GET", "/v1/organization/projects")] == [("GET", "/v1/organization/projects")]
    assert ("GET", "/v1/organization/projects/p-1") in fake.calls  # confirmed by its own read, not a listing


def test_the_data_source_reads_its_query_from_stdin(acgw, monkeypatch, capsys):
    # data "external" passes the query as JSON on stdin; nothing travels in the shell string.
    monkeypatch.setattr(sys, "argv", ["acgw.py", "projects-ensure"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"users": json.dumps({"ocgw-alice": {"User": "alice"}})})))
    seen = {}

    def fake_ensure(users_json):
        seen["users"] = json.loads(users_json)
    # The helper's own command line, with projects_ensure swapped for a recorder.
    monkeypatch.setattr(acgw, "projects_ensure", fake_ensure)
    acgw.main()
    assert seen["users"] == {"ocgw-alice": {"User": "alice"}}


def test_the_helper_refuses_an_unknown_command():
    r = run_py([str(SRC), "no-such-command"], capture_output=True, text=True, timeout=60,  # nosec B603
                       env={"AWS_REGION": "us-east-1", "PATH": "/usr/bin:/bin"}, check=False)
    assert r.returncode != 0



def test_an_archive_of_a_project_already_gone_is_done(acgw, monkeypatch):
    fake = Mantle(acgw, [proj("p-1", "ocgw-alice")])
    fake.on_archive = lambda pid: (_ for _ in ()).throw(fake.err(404))
    monkeypatch.setattr(acgw, "mantle", fake)
    acgw.project_archive("ocgw-alice")


@pytest.mark.parametrize("status", [400, 403, 409, 429])
def test_a_refused_archive_is_an_error(acgw, monkeypatch, status):
    # Verified live (r91): archiving an archived project answers 200, so a refusal is a real one.
    fake = Mantle(acgw, [proj("p-1", "ocgw-alice")])
    fake.on_archive = lambda pid: (_ for _ in ()).throw(fake.err(status))
    monkeypatch.setattr(acgw, "mantle", fake)
    with pytest.raises(acgw.HelperError, match=f"HTTP {status}"):
        acgw.project_archive("ocgw-alice")


def test_an_archive_is_sent_as_idempotent(acgw, monkeypatch):
    # Verified live (r91): a second archive answers 200 again, so mantle() may resend it after a 5xx or a lost reply.
    seen = []
    monkeypatch.setattr(acgw, "_listed", lambda: [proj("p-1", "ocgw-alice")])
    monkeypatch.setattr(acgw, "mantle", lambda m, p, b=None, idempotent=True, empty_ok=False: seen.append(idempotent) or {"status": "archived"})
    acgw.project_archive("ocgw-alice")
    assert seen == [True]


@pytest.mark.parametrize("now,ok", [("archived", True), ("active", False)])
def test_an_archive_reply_without_a_status_is_checked_with_one_get(acgw, monkeypatch, now, ok):
    fake = Mantle(acgw, [proj("p-1", "ocgw-alice")])

    def empty(pid):
        fake.projects[0]["status"] = now
        return {}
    fake.on_archive = empty
    monkeypatch.setattr(acgw, "mantle", fake)
    if ok:
        acgw.project_archive("ocgw-alice")
    else:
        with pytest.raises(acgw.HelperError, match="still reports it live \\(active\\)"):
            acgw.project_archive("ocgw-alice")
    assert ("GET", "/v1/organization/projects/p-1") in fake.calls


def test_one_users_failed_create_does_not_stop_the_others(acgw, monkeypatch, capsys):
    # r91: the first failed create raised and the users after it got no project in that run.
    fake = Mantle(acgw)

    def create(body):
        if body["name"] == "ocgw-alice":
            raise fake.err(403)
        p = proj(f"p-{body['name']}", body["name"])
        fake.projects.append(p)
        return dict(p)
    fake.on_create = create
    monkeypatch.setattr(acgw, "mantle", fake)
    with pytest.raises(acgw.HelperError, match="ocgw-alice: HTTP 403"):
        acgw.projects_ensure(json.dumps({"ocgw-alice": {}, "ocgw-bob": {}, "ocgw-carol": {}}))
    assert {p["name"] for p in fake.projects} == {"ocgw-bob", "ocgw-carol"}



def test_the_plan_time_lookup_never_writes_tags(acgw, monkeypatch, capsys):
    # r92: a retag at plan time disagreed with the ledger's group (seeded at apply) whenever a plan was not applied.
    fake = Mantle(acgw, [proj("p-a", "ocgw-alice", tags={"Project": "opencode-agentcore-gateway", "User": "ocgw-alice", "Group": "team-a"})])
    ensure(acgw, monkeypatch, capsys, fake, ["ocgw-alice"])
    assert [c for c in fake.calls if c[0] == "POST"] == []


def test_retag_reads_one_listing_and_writes_only_what_differs(acgw, monkeypatch):
    # r93: one step for every project (one process, one listing), instead of a process and a GET per user.
    fake = Mantle(acgw, [{**proj("p-a", "ocgw-alice"), "tags": {"User": "alice", "Group": "team-b", "aws:x": "y"}},
                         {**proj("p-b", "ocgw-bob"), "tags": {"User": "bob", "Group": "team-a"}}])
    monkeypatch.setattr(acgw, "mantle", fake)
    acgw.projects_retag(json.dumps({"p-a": {"User": "alice", "Group": "team-b"}, "p-b": {"User": "bob", "Group": "team-b"}}))
    assert [c for c in fake.calls if c[0] == "GET"] == [("GET", "/v1/organization/projects")]
    assert [c for c in fake.calls if c[0] == "POST"] == [("POST", "/v1/organization/projects/p-b")]


def test_retag_takes_an_empty_success_reply_and_reports_failures_together(acgw, monkeypatch):
    seen = []

    def mantle(m, p, b=None, idempotent=True, empty_ok=False):
        if m == "GET":
            return {"data": [proj("p-a", "ocgw-alice"), proj("p-b", "ocgw-bob")], "has_more": False}
        seen.append((p, empty_ok))
        if p.endswith("p-a"):
            raise acgw.MantleError(400, "refused")
        return {}
    monkeypatch.setattr(acgw, "mantle", mantle)
    with pytest.raises(acgw.HelperError, match="p-a: refused"):
        acgw.projects_retag(json.dumps({"p-a": {"Group": "x"}, "p-b": {"Group": "y"}}))
    assert seen == [("/v1/organization/projects/p-a", True), ("/v1/organization/projects/p-b", True)]


def test_a_listing_that_repeats_its_cursor_is_an_error_not_a_loop(acgw, monkeypatch):
    monkeypatch.setattr(acgw, "mantle", lambda *a, **k: {"data": [], "has_more": True, "last_id": "p-1"})
    with pytest.raises(acgw.MantleError, match="no new last_id"):
        acgw._projects()


def test_retag_sends_only_the_owned_keys_that_changed(acgw, monkeypatch):
    # r94 (verified live): a tag write merges, so only the changed owned keys are sent; CostCenter and Mantle's own
    # tags are never touched (an aws: key would be refused).
    fake = Mantle(acgw, [{**proj("p-a", "ocgw-alice"), "tags": {"User": "alice", "Group": "team-a", "CostCenter": "42", "aws:createdBy": "x"}}])
    monkeypatch.setattr(acgw, "mantle", fake)
    acgw.projects_retag(json.dumps({"p-a": {"User": "alice", "Group": "team-b"}}))
    assert fake.sent == {"Group": "team-b"}
    assert fake.projects[0]["tags"] == {"User": "alice", "Group": "team-b", "CostCenter": "42", "aws:createdBy": "x"}


def test_retag_removes_an_owned_key_terraform_no_longer_sets(acgw, monkeypatch):
    fake = Mantle(acgw, [{**proj("p-a", "ocgw-alice"), "tags": {"User": "alice", "Group": "team-a", "Component": "mantle-project"}}])
    monkeypatch.setattr(acgw, "mantle", fake)
    acgw.projects_retag(json.dumps({"p-a": {"User": "alice", "Group": "team-a"}}))
    assert fake.sent == {"Component": None} and "Component" not in fake.projects[0]["tags"]


def test_retag_fails_for_a_project_missing_from_the_listing_and_skips_a_dead_one(acgw, monkeypatch):
    # r94: a skipped missing project was recorded as applied and never retried.
    fake = Mantle(acgw, [proj("p-dead", "ocgw-bob", status="failed")])
    monkeypatch.setattr(acgw, "mantle", fake)
    with pytest.raises(acgw.HelperError, match="p-gone: no such project"):
        acgw.projects_retag(json.dumps({"p-gone": {"Group": "x"}, "p-dead": {"Group": "y"}}))
    assert [c for c in fake.calls if c[0] == "POST"] == []


def test_retag_reads_one_variable_per_project(acgw, monkeypatch):
    # r94: one OCGW_TAGS map for every user could pass Linux's 128 KiB limit on a single environment string.
    fake = Mantle(acgw, [{**proj("p-a", "ocgw-alice"), "tags": {}}, {**proj("p-b", "ocgw-bob"), "tags": {}}])
    monkeypatch.setattr(acgw, "mantle", fake)
    monkeypatch.setenv("OCGW_TAGS_N", "2")
    monkeypatch.setenv("OCGW_TAGS_0", json.dumps({"p-a": {"Group": "a"}}))
    monkeypatch.setenv("OCGW_TAGS_1", json.dumps({"p-b": {"Group": "b"}}))
    acgw.projects_retag()
    assert [p["tags"] for p in fake.projects] == [{"Group": "a"}, {"Group": "b"}]


@pytest.mark.parametrize("reply,ok", [({"id": "p-1"}, False), ({"id": "p-1", "archived_at": 5}, True)])
def test_an_archive_with_no_status_counts_only_with_an_archived_at(acgw, monkeypatch, reply, ok):
    # r94: one rule for a status-less project everywhere: live unless archived_at says otherwise.
    def mantle(m, p, b=None, idempotent=True, empty_ok=False):
        if p.startswith("/v1/organization/projects?"):
            return {"data": [proj("p-1", "ocgw-alice")], "has_more": False}
        return dict(reply)
    monkeypatch.setattr(acgw, "mantle", mantle)
    if ok:
        acgw.project_archive("ocgw-alice")
    else:
        with pytest.raises(acgw.HelperError, match="still reports it live"):
            acgw.project_archive("ocgw-alice")



def test_retag_reads_only_the_variables_terraform_set(acgw, monkeypatch):
    # r95: an OCGW_TAGS_99 left in the operator's shell became a retag target.
    fake = Mantle(acgw, [{**proj("p-a", "ocgw-alice"), "tags": {}}, {**proj("p-old", "ocgw-old"), "tags": {}}])
    monkeypatch.setattr(acgw, "mantle", fake)
    monkeypatch.setenv("OCGW_TAGS_N", "1")
    monkeypatch.setenv("OCGW_TAGS_0", json.dumps({"p-a": {"Group": "a"}}))
    monkeypatch.setenv("OCGW_TAGS_99", json.dumps({"p-old": {"Group": "stale"}}))
    acgw.projects_retag()
    assert fake.projects[1]["tags"] == {}


def test_retag_reads_a_project_the_listing_does_not_show_yet(acgw, monkeypatch):
    # r95: a project made at plan time and not yet listed at apply failed the step; it is read directly.
    fake = Mantle(acgw, [{**proj("p-new", "ocgw-dan"), "tags": {"Group": "a"}}])
    real = fake.__call__
    monkeypatch.setattr(acgw, "mantle", lambda m, p, *a, **k: {"data": [], "has_more": False}
                        if m == "GET" and p.startswith("/v1/organization/projects?") else real(m, p, *a, **k))
    acgw.projects_retag(json.dumps({"p-new": {"Group": "b"}}))
    assert fake.projects[0]["tags"] == {"Group": "b"}


def test_the_listing_cursor_is_url_encoded(acgw, monkeypatch):
    # r95: a cursor with & or + was sent as is, garbling the next page's query (and its signature).
    paths = []

    def mantle(m, p, *a, **k):
        paths.append(p)
        return {"data": [], "has_more": len(paths) == 1, "last_id": "a&b+c"}
    monkeypatch.setattr(acgw, "mantle", mantle)
    acgw._projects()
    assert paths[1].endswith("after=a%26b%2Bc")


def test_missing_credentials_stop_with_a_message(acgw, monkeypatch):
    # r95: no credentials ended in an AttributeError traceback.
    monkeypatch.setattr(acgw.S, "get_credentials", lambda: None)
    with pytest.raises(acgw.HelperError, match="no AWS credentials"):  # r98: a message at __main__, not mid-loop
        acgw.mantle("GET", "/v1/organization/projects")


def test_the_owned_tag_keys_match_the_terraform_tags():
    # r95: TAG_KEYS and policy.tf local.project_tags are two copies of one list; this keeps them equal.
    import re
    tf = (SRC.parents[1] / "policy.tf").read_text()
    block = re.search(r"project_tags = \{ for u, g in var.users : u => \{(.*?)\} \}", tf, re.S).group(1)
    keys = tuple(re.findall(r"(\w+) *=", block))
    spec = importlib.util.spec_from_file_location("acgw_keys", str(SRC))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert keys == mod.TAG_KEYS


def test_helper_paths_in_terraform_are_relative():
    # r95: an absolute helper path stored in state broke destroy steps applied from another checkout or machine.
    tf = "".join((SRC.parents[1] / f).read_text() for f in ("policy.tf", "fallback.tf", "layer.tf"))
    assert "abspath(" not in tf



def test_every_duplicate_is_tried_even_when_one_archive_fails(acgw, monkeypatch):
    # r96: the first failure ended the loop, leaving the second live duplicate unarchived.
    fake = Mantle(acgw, [proj("p-1", "ocgw-alice", created_at=1), proj("p-2", "ocgw-alice", created_at=2)])

    def archive(pid):
        if pid == "p-1":
            raise fake.err(409)
        next(p for p in fake.projects if p["id"] == pid)["status"] = "archived"
        return {"status": "archived"}
    fake.on_archive = archive
    monkeypatch.setattr(acgw, "mantle", fake)
    with pytest.raises(acgw.HelperError, match="p-1: HTTP 409"):
        acgw.project_archive("ocgw-alice")
    assert fake.projects[1]["status"] == "archived"


def test_a_failed_direct_read_does_not_stop_the_other_retags(acgw, monkeypatch):
    # r96: _get_project ran outside the per-project try, so its error ended the loop.
    fake = Mantle(acgw, [{**proj("p-b", "ocgw-bob"), "tags": {}}])
    real = fake.__call__

    def mantle(m, p, *a, **k):
        if m == "GET" and p.endswith("/p-a"):
            raise acgw.MantleError(503, "unavailable")
        return real(m, p, *a, **k)
    monkeypatch.setattr(acgw, "mantle", mantle)
    with pytest.raises(acgw.HelperError, match="p-a: unavailable"):
        acgw.projects_retag(json.dumps({"p-a": {"Group": "x"}, "p-b": {"Group": "y"}}))
    assert fake.projects[0]["tags"] == {"Group": "y"}


def test_the_interpreter_is_chosen_when_the_command_runs_with_the_helpers_requirements(tmp_path):
    # r96/r97: chosen at run time by helpers/python.sh (not a path stored in state); the uv fallback carries requests
    # too, and a checkout under a folder with a space works.
    import shutil
    helpers = tmp_path / "a dir" / "terraform" / "helpers"
    helpers.mkdir(parents=True)
    shutil.copy(SRC.parent / "python.sh", helpers / "python.sh")
    shutil.copy(SRC.parent / "agentcore_wait.py", helpers / "agentcore_wait.py")  # python.sh reads MIN_BOTO3 there
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "uv").write_text("#!/bin/sh\necho uv \"$@\"\n")
    (fake_bin / "uv").chmod(0o755)
    (fake_bin / "python3").write_text("#!/bin/sh\nexit 1\n")  # a python3 without the requirements: passed over
    (fake_bin / "python3").chmod(0o755)
    env = {"PATH": f"{fake_bin}:/usr/bin:/bin"}
    out = subprocess.run(["sh", str(helpers / "python.sh"), "x.py"], capture_output=True, text=True, env=env, timeout=30,  # nosec B603
                         check=True).stdout
    assert out.split() == ["uv", "run", "--no-project", "--no-build", "--exclude-newer", "2026-11-24T00:00:00Z",  # r196
                           "--with", "boto3==1.44.1", "--with", "requests==2.34.2", "python", "x.py"]
    venv = tmp_path / "a dir" / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").write_text("#!/bin/sh\necho venv \"$@\"\n")
    (venv / "python").chmod(0o755)
    out = subprocess.run(["sh", str(helpers / "python.sh"), "x.py"], capture_output=True, text=True, env=env, timeout=30,  # nosec B603
                         check=True).stdout
    assert out.split() == ["venv", "x.py"]
    tf = (SRC.parents[1] / "policy.tf").read_text()
    assert "helpers/python.sh" in tf


def test_a_credentials_error_mid_retag_still_reports_every_failure(acgw, monkeypatch):
    # r97: a SystemExit from mantle() (credentials expired) ended the loop and lost the failures already collected.
    fake = Mantle(acgw, [{**proj("p-a", "ocgw-alice"), "tags": {}}, {**proj("p-b", "ocgw-bob"), "tags": {}}])
    real = fake.__call__

    def mantle(m, p, *a, **k):
        if m == "POST":
            raise acgw.HelperError("no AWS credentials")
        return real(m, p, *a, **k)
    monkeypatch.setattr(acgw, "mantle", mantle)
    with pytest.raises(acgw.HelperError) as e:
        acgw.projects_retag(json.dumps({"p-a": {"Group": "x"}, "p-b": {"Group": "y"}}))
    assert "p-a: no AWS credentials" in str(e.value) and "p-b: no AWS credentials" in str(e.value)



def test_seed_groups_writes_every_user_in_one_process_and_reports_failures_together(acgw, monkeypatch):
    # r98: one step for every user instead of one helper process each.
    import botocore.exceptions
    calls = []

    class Ddb:
        def update_item(self, **kw):
            user = kw["Key"]["pk"]["S"]
            calls.append((user, kw["ExpressionAttributeValues"][":g"]["S"]))
            if user == "USER#ghost":
                raise botocore.exceptions.ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
    monkeypatch.setattr(acgw.S, "client", lambda name: Ddb())
    monkeypatch.setenv("OCGW_GROUPS_N", "3")
    for i, (u, g) in enumerate([("alice", "a"), ("bob", "b"), ("ghost", "c")]):
        monkeypatch.setenv(f"OCGW_GROUPS_{i}", json.dumps({u: g}))
    with pytest.raises(acgw.HelperError, match="ghost"):
        acgw.seed_groups("t")
    assert calls == [("USER#alice", "a"), ("USER#bob", "b"), ("USER#ghost", "c")]


def test_a_helper_error_reaches_terraform_as_a_message_not_a_traceback():
    r = run_py([str(SRC), "projects-retag"], capture_output=True, text=True, timeout=60,  # nosec B603
                       env={"AWS_REGION": "us-east-1", "PATH": "/usr/bin:/bin", "OCGW_TAGS_N": "1",
                            "OCGW_TAGS_0": json.dumps({"p-a": {"Group": "x"}}), "AWS_CONFIG_FILE": "/nonexistent",
                            "AWS_SHARED_CREDENTIALS_FILE": "/nonexistent", "AWS_EC2_METADATA_DISABLED": "true"}, check=False)
    assert r.returncode != 0 and "Traceback" not in r.stderr and "no AWS credentials" in r.stderr


def test_python_sh_passes_over_a_venv_with_an_old_boto3(tmp_path):
    # r98: the gateway-rule fields need boto3 >= 1.43.118; an older .venv falls through to uv instead of failing.
    import shutil
    helpers = tmp_path / "repo" / "terraform" / "helpers"
    helpers.mkdir(parents=True)
    shutil.copy(SRC.parent / "python.sh", helpers / "python.sh")
    shutil.copy(SRC.parent / "agentcore_wait.py", helpers / "agentcore_wait.py")  # python.sh reads MIN_BOTO3 there
    venv = tmp_path / "repo" / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").write_text("#!/bin/sh\nif [ \"$1\" = -c ]; then exit 1; fi\necho venv \"$@\"\n")  # too old
    (venv / "python").chmod(0o755)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "uv").write_text("#!/bin/sh\necho uv\n")
    (fake_bin / "uv").chmod(0o755)
    (fake_bin / "python3").write_text("#!/bin/sh\nexit 1\n")
    (fake_bin / "python3").chmod(0o755)
    out = subprocess.run(["sh", str(helpers / "python.sh"), "x.py"], capture_output=True, text=True, timeout=30,  # nosec B603
                         env={"PATH": f"{fake_bin}:/usr/bin:/bin"}, check=True).stdout
    assert out.split() == ["uv"]


def test_python_sh_prefers_a_fitting_python3_to_uv(tmp_path):
    # r99: uv first meant an offline machine with uv and a suitable python3 could not run any helper.
    import shutil
    helpers = tmp_path / "repo" / "terraform" / "helpers"
    helpers.mkdir(parents=True)
    shutil.copy(SRC.parent / "python.sh", helpers / "python.sh")
    shutil.copy(SRC.parent / "agentcore_wait.py", helpers / "agentcore_wait.py")  # python.sh reads MIN_BOTO3 there
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "uv").write_text("#!/bin/sh\necho uv\n")
    (fake_bin / "uv").chmod(0o755)
    (fake_bin / "python3").write_text("#!/bin/sh\nif [ \"$1\" = -c ]; then exit 0; fi\necho python3 \"$@\"\n")
    (fake_bin / "python3").chmod(0o755)
    out = subprocess.run(["sh", str(helpers / "python.sh"), "x.py"], capture_output=True, text=True, timeout=30,  # nosec B603
                         env={"PATH": f"{fake_bin}:/usr/bin:/bin"}, check=True).stdout
    assert out.split() == ["python3", "x.py"]


def test_python_sh_fit_check_reads_versions_without_importing_boto3():
    # r99: the check imported boto3 and requests on every helper call only to read a version.
    s = (SRC.parent / "python.sh").read_text()
    assert "importlib.metadata" in s and "import sys, boto3" not in s


def test_require_boto3_stops_an_old_interpreter_with_a_message():
    # r99: var.python / var.rule_python set by hand bypassed python.sh's check; the helpers check for themselves.
    import agentcore_wait
    agentcore_wait.require_boto3("1.43.118")
    agentcore_wait.require_boto3("1.44.0")
    with pytest.raises(SystemExit, match="boto3 1.43.115 is older than 1.43.118"):
        agentcore_wait.require_boto3("1.43.115")


def test_seed_groups_collects_a_botocore_error_and_goes_on(acgw, monkeypatch):
    # r99: only ClientError was caught; an EndpointConnectionError ended the loop with a traceback.
    import botocore.exceptions
    calls = []

    class Ddb:
        def update_item(self, **kw):
            user = kw["Key"]["pk"]["S"]
            calls.append(user)
            if user == "USER#alice":
                raise botocore.exceptions.EndpointConnectionError(endpoint_url="https://dynamodb")
    monkeypatch.setattr(acgw.S, "client", lambda name: Ddb())
    with pytest.raises(acgw.HelperError, match="alice"):
        acgw.seed_groups("t", json.dumps({"alice": "a", "bob": "b"}))
    assert calls == ["USER#alice", "USER#bob"]


def test_main_turns_network_and_boto3_errors_into_a_message():
    # r99: a RequestException past the retries, or a BotoCoreError, reached Terraform as a traceback.
    import botocore.exceptions
    import requests
    assert requests.RequestException in _failures() and botocore.exceptions.BotoCoreError in _failures()
    assert "except (*FAILURES, KeyError, ValueError, TypeError) as e:  # a message for Terraform" in SRC.read_text()


def _failures():
    spec = importlib.util.spec_from_file_location("acgw_failures", str(SRC))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.FAILURES


def test_layer_zip_path_stays_relative(tmp_path):
    # r99: an absolute zip path in state replaced the layer when applied from another checkout.
    tf = (SRC.parents[1] / "layer.tf").read_text()
    assert "abspath(" not in tf
    s = (SRC.parent / "build_layer.py").read_text()
    assert 'str(Path(q["dir"]) / out.name)' in s


def test_python_sh_takes_the_boto3_minimum_from_agentcore_wait():
    # r100: the minimum was written out in python.sh as well as in MIN_BOTO3.
    s = (SRC.parent / "python.sh").read_text()
    assert "1.43.118" not in s and "MIN_BOTO3" in s


def test_retag_sends_only_the_owned_tag_keys(acgw, monkeypatch):
    # r100: a key outside TAG_KEYS was sent, and never removed later.
    fake = Mantle(acgw, [{**proj("p-a", "ocgw-alice"), "tags": {}}])
    monkeypatch.setattr(acgw, "mantle", fake)
    acgw.projects_retag(json.dumps({"p-a": {"Group": "g", "Stray": "x"}}))
    assert fake.projects[0]["tags"] == {"Group": "g"}


def test_layer_zip_is_hashed_each_run_and_built_reproducibly_in_a_per_run_folder():
    # r100: a fixed .zip.part path was shared by concurrent plans; every plan re-hashed the whole zip.
    # r101: the cached digest could go stale against the zip; the zip is hashed every run again.
    s = (SRC.parent / "build_layer.py").read_text()
    assert 'Path(tmp) / "layer.zip.part"' in s and "read_text" not in s and '"--exclude-newer"' in s
    assert 'p.name != "RECORD"' in s and '!= "bin"' in s  # r102: bin/ scripts carry the build's temp path


def test_layer_tf_refuses_a_boto3_older_than_the_minimum():
    # r101: layer.tf's figure is tied to agentcore_wait.MIN_BOTO3, so raising one without the other fails here.
    import agentcore_wait
    tf = (SRC.parents[1] / "layer.tf").read_text()
    a, b, c = agentcore_wait.MIN_BOTO3
    assert f">= {int(f'{a:05d}{b:05d}{c:05d}')}" in tf
    assert f'default     = "{a}.{b}.{c}"' in tf
    assert tf.count(f"{a}.{b}.{c} or later") == 2  # the description and the error message


def test_retag_reports_every_project_when_the_listing_failed(acgw, monkeypatch):
    # r101: each project got its own full retry cycle after the listing had already failed.
    calls = []

    def mantle(m, p, *a, **k):
        calls.append((m, p))
        raise acgw.MantleError(503, "unavailable")
    monkeypatch.setattr(acgw, "mantle", mantle)
    # r106: the retag's own early stop went; the breaker in mantle() makes these calls quick, and each is reported.
    with pytest.raises(acgw.HelperError) as e:
        acgw.projects_retag(json.dumps({"p-a": {"Group": "x"}, "p-b": {"Group": "y"}}))
    assert "p-a: unavailable" in str(e.value) and "p-b: unavailable" in str(e.value)


def test_retag_goes_on_past_a_refused_read_when_mantle_answers(acgw, monkeypatch):
    # r102: a 403 on one project (Mantle reachable) stopped every other retag; and earlier failures were dropped.
    fake = Mantle(acgw, [{**proj("p-b", "ocgw-bob"), "tags": {}}])
    real = fake.__call__

    def mantle(m, p, *a, **k):
        if m == "GET" and "?" in p:
            raise acgw.MantleError(200, "no new last_id")
        if m == "GET" and p.endswith("/p-a"):
            raise acgw.MantleError(403, "forbidden")
        return real(m, p, *a, **k)
    monkeypatch.setattr(acgw, "mantle", mantle)
    with pytest.raises(acgw.HelperError, match="p-a: forbidden"):
        acgw.projects_retag(json.dumps({"p-a": {"Group": "x"}, "p-b": {"Group": "y"}}))
    assert fake.projects[0]["tags"] == {"Group": "y"}


def test_main_reports_an_unknown_command_or_a_missing_variable_without_a_traceback():
    env = {"AWS_REGION": "us-east-1", "PATH": "/usr/bin:/bin", "AWS_EC2_METADATA_DISABLED": "true"}
    r = run_py([str(SRC), "seed-group"], capture_output=True, text=True, timeout=60,  # nosec B603
                       env=env, check=False)
    assert r.returncode != 0 and "Traceback" not in r.stderr and "unknown command 'seed-group'" in r.stderr
    r = run_py([str(SRC), "seed-groups", "@env:OCGW_TABLE"], capture_output=True, text=True,  # nosec B603
                       timeout=60, env=env, check=False)
    assert r.returncode != 0 and "Traceback" not in r.stderr and "OCGW_TABLE" in r.stderr


def test_after_an_outage_later_mantle_calls_fail_fast(acgw, monkeypatch):
    # r103: an outage after a good listing cost a full retry cycle on every later call; the breaker stops that.
    import requests
    sends = []

    def request(*a, **k):
        sends.append(1)
        raise requests.ConnectionError("unreachable")  # a network failure: trips the breaker at once
    monkeypatch.setattr(acgw.requests, "request", request)
    monkeypatch.setattr(acgw.time, "sleep", lambda s: None)
    monkeypatch.setattr(acgw, "_OUT_OF_REACH", None)
    with pytest.raises(requests.ConnectionError):
        acgw.mantle("GET", "/v1/organization/projects/p-a")
    first = len(sends)
    with pytest.raises(acgw.MantleError, match="out of reach earlier in this run"):
        acgw.mantle("GET", "/v1/organization/projects/p-b")
    assert len(sends) == first


def test_projects_ensure_with_bad_stdin_is_a_message():
    r = run_py([str(SRC), "projects-ensure"], input="not json", capture_output=True, text=True,  # nosec B603
                       timeout=60, env={"AWS_REGION": "us-east-1", "PATH": "/usr/bin:/bin", "AWS_EC2_METADATA_DISABLED": "true"},
                       check=False)
    assert r.returncode != 0 and "Traceback" not in r.stderr and "projects-ensure" in r.stderr


def test_layer_hash_is_of_contents_and_old_zips_wait_a_week():
    s = (SRC.parent / "build_layer.py").read_text()
    assert "info.create_system = 3" in s and "z.namelist()" in s and "7 * 86400" in s and "as_posix())" in s


def test_list_all_follows_next_tokens_and_takes_the_first_key_present():
    import agentcore_wait
    pages = {None: {"items": [1, 2], "nextToken": "t"}, "t": {"items": [3]}}
    assert agentcore_wait.list_all(lambda nextToken=None, **kw: pages[nextToken], ("rateLimits", "items"), g="x") == [1, 2, 3]


def test_projects_the_breaker_refused_are_each_reported(acgw, monkeypatch):
    # r104: r103's breaker raised MantleError(None), which _outage() did not count, so every project was tried.
    calls = []

    def mantle(m, p, *a, **k):
        calls.append(p)
        if "?" in p:
            raise acgw.MantleError(503, "unavailable")
        raise acgw.MantleOutage("not sent")
    monkeypatch.setattr(acgw, "mantle", mantle)
    with pytest.raises(acgw.HelperError, match="not sent"):  # r106: each MantleOutage is collected and reported
        acgw.projects_retag(json.dumps({"p-a": {"Group": "x"}, "p-b": {"Group": "y"}}))


def test_a_5xx_that_outlasts_the_retries_trips_the_breaker(acgw, monkeypatch):
    class R:
        status_code, text, content = 503, "busy", b"busy"
    sends = []
    monkeypatch.setattr(acgw.requests, "request", lambda *a, **k: sends.append(1) or R())
    monkeypatch.setattr(acgw, "_OUT_OF_REACH", None)
    # r105: one call that keeps failing can be one bad project; the second in a row is the service.
    for pid in ("p-a", "p-b"):
        with pytest.raises(acgw.MantleError) as e:
            acgw.mantle("GET", f"/v1/organization/projects/{pid}")
        assert not isinstance(e.value, acgw.MantleOutage)
    first = len(sends)
    with pytest.raises(acgw.MantleOutage):
        acgw.mantle("GET", "/v1/organization/projects/p-c")
    assert len(sends) == first


def test_list_all_stops_on_a_repeated_token():
    import agentcore_wait
    with pytest.raises(RuntimeError, match="repeated nextToken"):
        agentcore_wait.list_all(lambda **kw: {"items": [1], "nextToken": "same"}, ("items",))


def test_layer_zip_in_use_is_touched_and_cleanup_tolerates_a_vanished_file():
    s = (SRC.parent / "build_layer.py").read_text()
    assert "os.utime(out)" in s and "except FileNotFoundError:" in s


def test_rule_spec_missing_a_key_is_a_message():
    env = {"PATH": "/usr/bin:/bin", "RULE_SPEC": json.dumps({"region": "us-east-1", "gateway": "gw-1"}),
           "AWS_EC2_METADATA_DISABLED": "true"}
    r = run_py([str(SRC.parent / "gateway_rule.py"), "delete"], capture_output=True, text=True,  # nosec B603
                       timeout=60, env=env, check=False)
    assert r.returncode != 0 and "Traceback" not in r.stderr and "lacks description" in r.stderr


def test_one_project_failing_does_not_trip_the_breaker_when_others_answer(acgw, monkeypatch):
    class R:
        def __init__(self, code):
            self.status_code, self.text, self.content = code, "{}", b"{}"

        def json(self):
            return {}
    codes = iter([503] * 8 + [200, 200])
    monkeypatch.setattr(acgw.requests, "request", lambda *a, **k: R(next(codes)))
    monkeypatch.setattr(acgw, "_OUT_OF_REACH", None)
    monkeypatch.setattr(acgw, "_EXHAUSTED", 0)
    with pytest.raises(acgw.MantleError):
        acgw.mantle("GET", "/v1/organization/projects/bad")
    assert acgw.mantle("GET", "/v1/organization/projects/good") == {}
    assert acgw._EXHAUSTED == 0 and acgw._OUT_OF_REACH is None


def test_env_chunks_names_a_missing_chunk(acgw, monkeypatch):
    monkeypatch.setenv("OCGW_TAGS_N", "2")
    monkeypatch.setenv("OCGW_TAGS_0", json.dumps({"p-a": {"Group": "x"}}))
    monkeypatch.delenv("OCGW_TAGS_1", raising=False)
    with pytest.raises(acgw.HelperError, match="OCGW_TAGS_1"):
        acgw._env_chunks("OCGW_TAGS")


def test_gateway_rule_reports_service_errors_as_messages():
    s = (SRC.parent / "gateway_rule.py").read_text()
    assert "except (RuntimeError, botocore.exceptions.ClientError, botocore.exceptions.BotoCoreError) as e:" in s


def test_one_slow_project_does_not_trip_the_breaker(acgw, monkeypatch):
    # r106: a read timeout reached Mantle; one such call may be one slow project, so it counts like a 5xx.
    import requests
    monkeypatch.setattr(acgw.requests, "request", lambda *a, **k: (_ for _ in ()).throw(requests.ReadTimeout("slow")))
    monkeypatch.setattr(acgw, "_OUT_OF_REACH", None)
    monkeypatch.setattr(acgw, "_EXHAUSTED", 0)
    with pytest.raises(requests.ReadTimeout):
        acgw.mantle("GET", "/v1/organization/projects/slow")
    assert acgw._OUT_OF_REACH is None
    with pytest.raises(requests.ReadTimeout):
        acgw.mantle("GET", "/v1/organization/projects/also-slow")
    assert acgw._OUT_OF_REACH  # two in a row: the service


def test_list_all_takes_a_null_list_as_empty():
    import agentcore_wait
    assert agentcore_wait.list_all(lambda **kw: {"gatewayRules": None}, ("gatewayRules",)) == []


def test_rule_spec_with_a_bad_fallback_entry_is_a_message():
    spec = {"region": "us-east-1", "gateway": "gw-1", "description": "d", "priority": 1, "match": ["m"], "target": "t",
            "model": "m", "status_codes": [], "errors": [], "first_byte_timeout_ms": 1, "fallback": [{"target": "t"}]}
    env = {"PATH": "/usr/bin:/bin", "RULE_SPEC": json.dumps(spec), "AWS_EC2_METADATA_DISABLED": "true"}
    r = run_py([str(SRC.parent / "gateway_rule.py"), "apply"], capture_output=True, text=True,  # nosec B603
                       timeout=60, env=env, check=False)
    assert r.returncode != 0 and "Traceback" not in r.stderr and "every fallback entry" in r.stderr


def test_a_retag_during_an_outage_sends_only_the_listing_retries(acgw, monkeypatch):
    # r107: with the real mantle(), an unreachable Mantle costs one call's retries, not one cycle per project.
    import requests
    sends = []

    def request(*a, **k):
        sends.append(a[1] if len(a) > 1 else k.get("url"))
        raise requests.ConnectionError("unreachable")
    monkeypatch.setattr(acgw.requests, "request", request)
    monkeypatch.setattr(acgw, "_OUT_OF_REACH", None)
    monkeypatch.setattr(acgw, "_EXHAUSTED", 0)
    with pytest.raises(acgw.HelperError) as e:
        acgw.projects_retag(json.dumps({f"p-{i}": {"Group": "x"} for i in range(5)}))
    assert len(sends) == 8  # the listing's 8 tries; the 5 direct reads are refused by the breaker unsent
    assert all(f"p-{i}: " in str(e.value) for i in range(5)) and "not sent" in str(e.value)


def test_rule_spec_list_fields_must_be_lists():
    spec = {"region": "us-east-1", "gateway": "gw-1", "description": "d", "priority": 1, "match": "ow/glm-5.3",
            "target": "t", "model": "m", "status_codes": [], "errors": [], "first_byte_timeout_ms": 1, "fallback": []}
    env = {"PATH": "/usr/bin:/bin", "RULE_SPEC": json.dumps(spec), "AWS_EC2_METADATA_DISABLED": "true"}
    r = run_py([str(SRC.parent / "gateway_rule.py"), "apply"], capture_output=True, text=True,  # nosec B603
                       timeout=60, env=env, check=False)
    assert r.returncode != 0 and "Traceback" not in r.stderr and "match as a list" in r.stderr


def test_paging_takes_the_first_non_empty_list_key():
    import agentcore_wait
    assert agentcore_wait.list_all(lambda **kw: {"rateLimits": [], "items": [1]}, ("rateLimits", "items")) == [1]


def test_a_malformed_request_is_a_message_at_once_and_not_an_outage(acgw, monkeypatch):
    # r108: InvalidURL and the like never succeed: one try, a HelperError, and the breaker stays open.
    import requests
    sends = []

    def request(*a, **k):
        sends.append(1)
        raise requests.exceptions.InvalidURL("bad host")
    monkeypatch.setattr(acgw.requests, "request", request)
    monkeypatch.setattr(acgw, "_OUT_OF_REACH", None)
    monkeypatch.setattr(acgw, "_EXHAUSTED", 0)
    with pytest.raises(acgw.HelperError, match="malformed"):
        acgw.mantle("GET", "/v1/organization/projects")
    assert sends == [1] and acgw._OUT_OF_REACH is None and acgw._EXHAUSTED == 0


def test_an_empty_aws_profile_counts_as_unset():
    env = {"PATH": "/usr/bin:/bin", "AWS_PROFILE": "", "AWS_EC2_METADATA_DISABLED": "true",
           "AWS_CONFIG_FILE": "/nonexistent", "AWS_SHARED_CREDENTIALS_FILE": "/nonexistent"}
    r = run_py([str(SRC), "no-such-command"], capture_output=True, text=True,  # nosec B603
                       timeout=60, env=env, check=False)
    assert "could not be found" not in r.stderr and "Traceback" not in r.stderr


def test_a_body_read_timeout_counts_like_a_read_timeout(acgw, monkeypatch):
    # r109: requests wraps a timeout while the body streams as ConnectionError(ReadTimeoutError).
    import requests
    import urllib3.exceptions as u3
    err = requests.ConnectionError(u3.ReadTimeoutError(None, "/x", "read timed out"))
    monkeypatch.setattr(acgw.requests, "request", lambda *a, **k: (_ for _ in ()).throw(err))
    monkeypatch.setattr(acgw, "_OUT_OF_REACH", None)
    monkeypatch.setattr(acgw, "_EXHAUSTED", 0)
    with pytest.raises(requests.ConnectionError):
        acgw.mantle("GET", "/v1/organization/projects/slow")
    assert acgw._OUT_OF_REACH is None  # one slow project does not trip it
    assert acgw._slow_reply(requests.exceptions.ChunkedEncodingError("cut"))
    assert not acgw._slow_reply(requests.ConnectionError("refused"))


def test_drop_empty_profile_removes_only_empty_values():
    import agentcore_wait
    env = {"AWS_PROFILE": "", "AWS_DEFAULT_PROFILE": "x", "OTHER": ""}
    agentcore_wait.drop_empty_profile(env)
    assert env == {"AWS_DEFAULT_PROFILE": "x", "OTHER": ""}


def test_gateway_rule_accepts_an_empty_aws_profile():
    spec = {"region": "us-east-1", "gateway": "gw-1", "description": "d"}
    env = {"PATH": "/usr/bin:/bin", "RULE_SPEC": json.dumps(spec), "AWS_EC2_METADATA_DISABLED": "true",
           "AWS_PROFILE": "", "AWS_DEFAULT_PROFILE": "", "AWS_CONFIG_FILE": "/nonexistent",
           "AWS_SHARED_CREDENTIALS_FILE": "/nonexistent"}
    r = run_py([str(SRC.parent / "gateway_rule.py"), "delete"], capture_output=True, text=True,  # nosec B603
                       timeout=60, env=env, check=False)
    assert "could not be found" not in r.stderr and "Traceback" not in r.stderr


def test_a_reset_before_any_reply_is_not_a_slow_reply(acgw):
    # r110: requests reports a reset or abort before the status line as ConnectionError(ProtocolError): trip at once.
    import requests
    import urllib3.exceptions as u3
    assert not acgw._slow_reply(requests.ConnectionError(u3.ProtocolError("Connection aborted.", ConnectionResetError())))
    assert acgw._slow_reply(requests.ConnectionError(u3.ReadTimeoutError(None, "/x", "read timed out")))


def test_an_error_with_no_message_still_trips_the_breaker(acgw, monkeypatch):
    # r113: str(e) == "" must not leave the breaker reading as open.
    import requests
    monkeypatch.setattr(acgw.requests, "request", lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError()))
    monkeypatch.setattr(acgw, "_OUT_OF_REACH", None)
    monkeypatch.setattr(acgw, "_EXHAUSTED", 0)
    with pytest.raises(requests.ConnectionError):
        acgw.mantle("GET", "/v1/organization/projects")
    assert acgw._OUT_OF_REACH
    with pytest.raises(acgw.MantleOutage):
        acgw.mantle("GET", "/v1/organization/projects/p-1")


TAGGED = {"Project": "opencode-agentcore-gateway", "Component": "mantle-project"}


def test_projects_ensure_never_adopts_a_same_named_project_of_another_user(acgw, monkeypatch, capsys):
    """r196 threat model T-070: stack "ocgw-dev" with user alice and stack "ocgw" with user dev-alice both name a
    project ocgw-dev-alice. Matching on the name alone made one user's calls bill the other's project."""
    other = proj("p-other", "ocgw-dev-alice", tags={**TAGGED, "User": "alice"})
    untagged = proj("p-none", "ocgw-dev-alice", tags={})
    fake = Mantle(acgw, [other, untagged])
    monkeypatch.setattr(acgw, "mantle", fake)
    acgw.projects_ensure(json.dumps({"ocgw-dev-alice": {**TAGGED, "User": "dev-alice", "Group": "ow"}}))
    out = capsys.readouterr()
    got = json.loads(json.loads(out.out)["ocgw-dev-alice"])
    assert got["id"] not in ("p-other", "p-none") and ("POST", "/v1/organization/projects") in fake.calls
    assert not [c for c in fake.calls if c[1].startswith("/v1/organization/projects/p-")]  # the others left alone
    assert "skipping 2 live Mantle project(s)" in out.err and "p-other" in out.err and "p-none" in out.err
    # Its own tagged project is still found, and nothing is created.
    fake = Mantle(acgw, [other, proj("p-mine", "ocgw-dev-alice", tags={**TAGGED, "User": "dev-alice"})])
    monkeypatch.setattr(acgw, "mantle", fake)
    acgw.projects_ensure(json.dumps({"ocgw-dev-alice": {**TAGGED, "User": "dev-alice"}}))
    assert json.loads(json.loads(capsys.readouterr().out)["ocgw-dev-alice"])["id"] == "p-mine"
    assert ("POST", "/v1/organization/projects") not in fake.calls


def test_project_archive_leaves_a_same_named_project_of_another_user_live(acgw, monkeypatch, capsys):
    """r196 threat model T-070: removing dev-alice from stack "ocgw" must not archive alice's ocgw-dev-alice of stack
    "ocgw-dev". Terraform passes the user as OCGW_USER from the resource's input."""
    fake = Mantle(acgw, [proj("p-other", "ocgw-dev-alice", tags={**TAGGED, "User": "alice"}),
                         proj("p-mine", "ocgw-dev-alice", tags={**TAGGED, "User": "dev-alice"}),
                         proj("p-none", "ocgw-dev-alice", tags={"User": "dev-alice"})])  # not this repo's project
    monkeypatch.setattr(acgw, "mantle", fake)
    monkeypatch.setenv("OCGW_USER", "dev-alice")
    acgw.project_archive("ocgw-dev-alice")
    archived = [c[1] for c in fake.calls if c[1].endswith("/archive")]
    assert archived == ["/v1/organization/projects/p-mine/archive"]
    assert "skipping 2 live Mantle project(s)" in capsys.readouterr().err
    tf = (SRC.parents[1] / "policy.tf").read_text().split('resource "terraform_data" "project_archive"', 1)[1].split("\n}\n", 1)[0]
    assert "user = each.key" in tf and 'OCGW_USER = lookup(self.input, "user", "")' in tf


def test_project_archive_without_a_user_falls_back_to_the_name(acgw, monkeypatch, capsys):
    """A destroy whose state predates the user input (removed in the same apply as the upgrade) still archives."""
    fake = Mantle(acgw, [proj("p-1", "ocgw-alice", tags={})])
    monkeypatch.setattr(acgw, "mantle", fake)
    acgw.project_archive("ocgw-alice")
    assert ("POST", "/v1/organization/projects/p-1/archive") in fake.calls
    assert "OCGW_USER not set" in capsys.readouterr().err
