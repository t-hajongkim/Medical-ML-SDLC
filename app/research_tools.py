"""Private runner tool adapters; replay never imports a report-derived checkout."""

import json
import os
from pathlib import Path
import sys


def application(name, number, work, *args):
    sys.path.insert(0, os.environ["GITHUB_WORKSPACE"])
    from app import research
    handler = {"plan-eda": research.plan_eda, "execute-eda": research.execute_eda,
               "apply-input-mapping": research.apply_input_mapping,
               "apply-recommendations": research.apply_ai}[name]
    return handler(number, work, *args)


def apply_input_mapping():
    import json
    import os
    from pathlib import Path
    work = Path("/tmp/gh-aw/research-recommendation")
    if (work / "publication.json").exists() or (work / "application.lock").exists():
        raise SystemExit("게시 적용이 시작된 뒤에는 입력 매핑 도구를 다시 실행하지 않습니다.")
    raw = os.environ["INPUT_MAPPING"]
    if len(raw.encode("utf-8")) > 65536:
        raise SystemExit("입력 매핑 JSON 크기가 허용 범위를 넘었습니다.")
    (work / "input-mapping-proposal.json").write_text(raw, encoding="utf-8")
    result = application('apply-input-mapping', int(os.environ["TARGET_NUMBER"]), work)
    print(json.dumps(result, ensure_ascii=False))


def plan_eda():
    import json
    import os
    from pathlib import Path
    work = Path("/tmp/gh-aw/research-recommendation")
    if (work / "publication.json").exists() or (work / "application.lock").exists():
        raise SystemExit("게시 적용이 시작된 뒤에는 계획 도구를 다시 실행하지 않습니다.")
    raw = os.environ["INPUT_PROPOSAL"]
    if len(raw.encode("utf-8")) > 65536:
        raise SystemExit("연구 조율안이 허용 크기를 넘었습니다.")
    (work / "coordination-proposal.json").write_text(raw, encoding="utf-8")
    result = application('plan-eda', int(os.environ["TARGET_NUMBER"]), work)
    print(json.dumps(result, ensure_ascii=False))


def execute_eda():
    import json
    import os
    import re
    from pathlib import Path
    work = Path("/tmp/gh-aw/research-recommendation")
    if (work / "publication.json").exists() or (work / "application.lock").exists():
        raise SystemExit("게시 적용이 시작된 뒤에는 EDA 실행 도구를 다시 실행하지 않습니다.")
    plan = os.environ["INPUT_PLAN_HASH"]
    if re.fullmatch(r"[0-9a-f]{64}", plan) is None:
        raise SystemExit("검증한 EDA 계획 hash가 필요합니다.")
    result = application('execute-eda', int(os.environ["TARGET_NUMBER"]), work, plan)
    print(json.dumps(result, ensure_ascii=False))


def apply_recommendations():
    import hashlib
    import json
    import os
    import re
    import subprocess
    from pathlib import Path

    work = Path("/tmp/gh-aw/research-recommendation")
    root = Path(os.environ["GITHUB_WORKSPACE"]).resolve()
    ready = work / "publication.json"
    target = os.environ["TARGET_NUMBER"]
    kind = os.environ["TARGET_KIND"]
    if not re.fullmatch(r"[1-9][0-9]{0,9}", target) or kind not in ("issue", "pull_request"):
        raise SystemExit("Invalid recommendation target.")
    number = int(target)
    operation = "create" if kind == "issue" else "update"
    pr = None if operation == "create" else number
    session = json.loads((work / "session.json").read_text(encoding="utf-8"))
    issue = session["issue_number"]
    directory = f"research/reports/issue-{issue}"
    names = {"ai-recommendations.json", "eda-plan.json", "evaluation-plan.json",
             "eda.json", "report.md", "recommendation-request.json",
             "input-schema.json", "input-mapping.json", "coordination-plan.json",
             "feedback-applied.json", "selection.json", "execution-plan.json"}
    if operation == "create":
        names.add("request.json")
    allowed = {f"{directory}/{name}" for name in names}
    if (type(session.get("schema_version")) is not int or session["schema_version"] != 2
            or type(issue) is not int or issue < 1
            or session.get("target_kind") != kind
            or type(session.get("target_number")) is not int or session["target_number"] != number
            or session["pull_request"] != pr
            or (pr is not None and type(session["pull_request"]) is not int)
            or (operation == "create" and issue != number)
            or session["repository"] != os.environ["GITHUB_REPOSITORY"]
            or session["report_dir"] != directory
            or session["head_branch"] != f"research/review-issue-{issue}"
            or not re.fullmatch(r"[0-9a-f]{40}", session["head_sha"])
            or not re.fullmatch(r"[0-9a-f]{40}", session["code_commit"])
            or not isinstance(session["allowed_outputs"], list)
            or not all(isinstance(path, str) for path in session["allowed_outputs"])
            or not set(session["allowed_outputs"]).issubset(allowed)):
        raise SystemExit("Invalid trusted recommendation session.")
    request = json.loads((work / "AI_REQUEST.json").read_text(encoding="utf-8"))
    raw = os.environ["INPUT_RECOMMENDATIONS"]
    if len(raw.encode("utf-8")) > 65536:
        raise SystemExit("Recommendation JSON is too large.")
    recommendation = json.loads(raw)
    if (set(recommendation) != {"schema_version", "provider", "context_hash", "status", "eda", "evaluation"}
            or recommendation["schema_version"] != 1
            or recommendation["provider"] != "github-copilot-cloud-agent"
            or recommendation["status"] != "provided"
            or recommendation["context_hash"] != request["context_hash"]):
        raise SystemExit("Recommendation envelope does not match the validated request.")
    recommendation_hash = hashlib.sha256(json.dumps(
        recommendation, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    def git(*args):
        return subprocess.run(["git", *args], cwd=root, check=True,
                              capture_output=True, text=True, encoding="utf-8").stdout.strip()
    def api(path):
        return json.loads(subprocess.run(
            ["gh", "api", f"repos/{session['repository']}/{path}"],
            check=True, capture_output=True, text=True, encoding="utf-8").stdout)
    def previous_body():
        if operation == "create":
            refs = api("git/matching-refs/heads/" + session["head_branch"])
            if (not isinstance(refs, list)
                    or any(ref.get("ref") == "refs/heads/" + session["head_branch"] for ref in refs)):
                raise SystemExit("Initial report branch already exists; never overwrite or recreate it.")
            return ""
        current = api(f"pulls/{pr}")
        if (type(current.get("number")) is not int or current["number"] != pr
                or current.get("state") != "open"
                or current.get("head", {}).get("sha") != session["head_sha"]
                or current["head"].get("ref") != session["head_branch"]
                or current["head"].get("repo", {}).get("full_name") != session["repository"]
                or not current.get("title", "").startswith(f"연구검토 #{issue}:")):
            raise SystemExit("PR changed after preparation; preserve human edits and start a fresh run.")
        return current.get("body") or ""
    def validate_commit(commit):
        if commit is None:
            if operation == "create" or git("rev-parse", "HEAD") != session["head_sha"]:
                raise SystemExit("Initial publication requires a complete report commit.")
            return
        changed = set(git("diff", "--name-only", session["head_sha"], commit, "--").splitlines())
        if (git("rev-parse", "HEAD") != commit
                or git("rev-parse", commit + "^") != session["head_sha"]
                or not changed or not changed.issubset(session["allowed_outputs"])):
            raise SystemExit("Publication is not the validated report-only commit.")
    def validate_body(body):
        if operation == "create":
            start, end = "**자동 생성 연구 보고서 · 시작**", "**자동 생성 연구 보고서 · 끝**"
            text = body.decode("utf-8")
            if (text.count(start) != 1 or text.count(end) != 1
                    or not text.startswith(start + "\n") or not text.rstrip().endswith("\n" + end)):
                raise SystemExit("Initial body must contain the complete generated report inside its automatic markers.")
    def select_branch(commit):
        if commit is not None:
            # No repository Python runs after selecting the report-derived tree.
            git("update-ref", "refs/heads/" + session["head_branch"], commit)
            if operation == "update":
                git("update-ref", "refs/remotes/origin/" + session["head_branch"], session["head_sha"])
            git("symbolic-ref", "HEAD", "refs/heads/" + session["head_branch"])
    def respond(meta):
        commit = meta["publication_commit"]
        refresh = meta["body_refresh_required"]
        print(json.dumps({
            "status": "validated_copilot_recommendations" if commit else ("body_refresh" if refresh else "unchanged"),
            "operation": operation, "target_kind": kind, "target_number": number,
            "issue_number": issue, "pull_request": pr, "head_branch": session["head_branch"],
            "publication_commit": commit,
            "publication_ready": operation == "create" or commit is not None or refresh,
            "create_required": operation == "create",
            "push_required": operation == "update" and commit is not None,
            "body_refresh_required": refresh,
            "pull_request_title": meta["pull_request_title"],
            "pull_request_body": (work / "pr-body.md").read_bytes().decode("utf-8")}, ensure_ascii=False))
    if ready.exists():
        meta = json.loads(ready.read_text(encoding="utf-8"))
        commit = meta.get("publication_commit")
        refresh_body = meta.get("body_refresh_required")
        if (type(meta.get("schema_version")) is not int or meta["schema_version"] != 2
                or meta.get("operation") != operation or meta.get("target_kind") != kind
                or type(meta.get("target_number")) is not int or meta["target_number"] != number
                or type(meta.get("issue_number")) is not int or meta["issue_number"] != issue
                or meta.get("repository") != session["repository"]
                or meta.get("pull_request") != pr
                or (pr is not None and type(meta["pull_request"]) is not int)
                or meta.get("head_branch") != session["head_branch"]
                or meta.get("previous_head_sha") != session["head_sha"]
                or meta.get("recommendations_sha256") != recommendation_hash
                or type(refresh_body) is not bool
                or (operation == "create" and refresh_body)
                or not isinstance(meta.get("pull_request_title"), str)
                or not meta["pull_request_title"].startswith(f"연구검토 #{issue}:")
                or (commit is not None and not re.fullmatch(r"[0-9a-f]{40}", commit))
                or git("rev-parse", "HEAD") != (commit or session["head_sha"])
                or git("rev-parse", "HEAD^{tree}") != meta.get("expected_tree_sha")
                or hashlib.sha256((work / "pr-body.md").read_bytes()).hexdigest() != meta.get("body_sha256")):
            raise SystemExit("Stored publication does not match this request; start a fresh run.")
        validate_commit(commit)
        validate_body((work / "pr-body.md").read_bytes())
        if (hashlib.sha256(previous_body().encode("utf-8")).hexdigest()
                != meta.get("previous_body_sha256")):
            raise SystemExit("PR changed after application; preserve human changes and start a fresh run.")
        select_branch(commit)
        respond(meta)
        raise SystemExit(0)
    lock = work / "application.lock"
    try:
        lock.mkdir()
    except FileExistsError:
        raise SystemExit("Application is already running or needs recovery. Retry the same request; do not apply concurrently.")
    destination = root / directory / "ai-recommendations.json"
    if destination.is_symlink() or destination.parent.is_symlink() or not destination.resolve().is_relative_to(root):
        raise SystemExit("Unsafe recommendation output path.")
    destination.write_text(json.dumps(recommendation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    old_body = previous_body()
    try:
        result = application("apply-recommendations", number, work)
    except (ValueError, RuntimeError, OSError, KeyError, TypeError):
        if git("rev-parse", "HEAD") == session["code_commit"]:
            lock.rmdir()
        raise SystemExit("Trusted recommendation validation/application failed; nothing was published.") from None
    commit = result["publication_commit"]
    refresh_body = result["body_refresh_required"]
    if (result.get("operation") != operation or result.get("target_kind") != kind
            or type(result.get("target_number")) is not int or result["target_number"] != number
            or type(result.get("issue_number")) is not int or result["issue_number"] != issue
            or result.get("pull_request") != pr
            or (pr is not None and type(result["pull_request"]) is not int)
            or result.get("head_branch") != session["head_branch"]
            or result.get("remote_publication_performed", False) is not False
            or (commit is not None and not re.fullmatch(r"[0-9a-f]{40}", commit))
            or type(refresh_body) is not bool
            or (operation == "create" and refresh_body)
            or type(result.get("create_required")) is not bool
            or result["create_required"] != (operation == "create")
            or type(result.get("push_required")) is not bool
            or result["push_required"] != (operation == "update" and commit is not None)
            or type(result.get("publication_ready")) is not bool
            or result["publication_ready"] != (operation == "create" or commit is not None or refresh_body)
            or not isinstance(result.get("pull_request_title"), str)
            or not result["pull_request_title"].startswith(f"연구검토 #{issue}:")):
        raise SystemExit("Unexpected trusted application result.")
    validate_commit(commit)
    body = (work / "pr-body.md").read_bytes()
    validate_body(body)
    if result.get("pull_request_body") != body.decode("utf-8"):
        raise SystemExit("Trusted body differs from the prepared report.")
    if operation == "update" and refresh_body != (commit is not None or body != old_body.encode("utf-8")):
        raise SystemExit("Unexpected trusted body-refresh flags.")
    expected_tree = git("rev-parse", (commit or session["head_sha"]) + "^{tree}")
    select_branch(commit)
    receipt = work / "publication.pending.json"
    receipt.write_text(json.dumps({
        "schema_version": 2, "operation": operation, "repository": session["repository"],
        "target_kind": kind, "target_number": number, "issue_number": issue, "pull_request": pr,
        "pull_request_title": result["pull_request_title"],
        "head_branch": session["head_branch"],
        "publication_commit": commit, "body_refresh_required": refresh_body,
        "previous_head_sha": session["head_sha"],
        "expected_tree_sha": expected_tree,
        "previous_body_sha256": hashlib.sha256(old_body.encode("utf-8")).hexdigest(),
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "recommendations_sha256": recommendation_hash,
    }), encoding="utf-8")
    receipt.replace(ready)
    respond(json.loads(ready.read_text(encoding="utf-8")))


def main():
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("Research tools are restricted to the Actions runner.")
    handlers = {"apply-input-mapping": apply_input_mapping, "plan-eda": plan_eda,
                "execute-eda": execute_eda, "apply-recommendations": apply_recommendations}
    handlers[sys.argv[1]]()


if __name__ == "__main__":
    main()
