"""Concrete final-roadmap writes with exact read-only outcome reconciliation."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Any

from tools import agent_epic_delivery as delivery
from tools import agent_epic_final_delivery as final
from tools.agent_epic_transport import GhTaskTransport


class GhFinalTransport(GhTaskTransport):
    @staticmethod
    def final_comment(request: final.Request) -> str:
        marker = hashlib.sha256(request.operation_id.encode()).hexdigest()
        return (
            f"ROADMAP DONE at {request.merge_sha}; protected roadmap branch retained.\n"
            f"<!-- epic-runner-final:{marker} -->"
        )

    def pull_request(self, request: final.Request) -> dict[str, Any]:
        value = self.json(
            (
                "gh",
                "pr",
                "view",
                str(request.pr),
                "--repo",
                request.repository,
                "--json",
                "number,state,isCrossRepository,baseRefName,baseRefOid,headRefName,headRefOid,mergeCommit",
            )
        )
        if not isinstance(value, dict):
            raise delivery.OutcomeUnknown
        if (
            value.get("number") != request.pr
            or value.get("isCrossRepository") is not False
            or value.get("baseRefName") != "main"
            or value.get("baseRefOid") != request.main_sha
            or value.get("headRefName") != request.roadmap_ref
            or value.get("headRefOid") != request.roadmap_sha
        ):
            raise delivery.OutcomeUnknown
        return value

    def effect(self, request: Any) -> dict[str, object]:
        if not isinstance(request, final.Request) or not final.valid_request(request):
            raise delivery.OutcomeUnknown
        repo = request.repository
        if request.operation == "create_roadmap_pr":
            value = self.write_json(
                (
                    "gh",
                    "api",
                    f"repos/{repo}/pulls",
                    "--method",
                    "POST",
                    "-f",
                    "base=main",
                    "-f",
                    f"head={request.roadmap_ref}",
                    "-f",
                    f"title=Roadmap #{request.epic}",
                    "-f",
                    "body="
                    + self.pr_body(
                        base_ref="main",
                        base_sha=request.main_sha,
                        head_ref=request.roadmap_ref,
                        head_sha=request.roadmap_sha,
                        epic=request.epic,
                        task=None,
                    ),
                ),
                request,
            )
            number = value.get("number") if isinstance(value, dict) else None
            if type(number) is not int or number <= 0:
                raise delivery.OutcomeUnknown
            bound = replace(request, pr=number, operation="merge_roadmap_pr")
            if self.pull_request(bound).get("state") != "OPEN":
                raise delivery.OutcomeUnknown
            return {"pr": number}
        if request.operation == "merge_roadmap_pr":
            self.write_command(
                (
                    "gh",
                    "pr",
                    "merge",
                    str(request.pr),
                    "--repo",
                    repo,
                    "--merge",
                    "--match-head-commit",
                    request.roadmap_sha,
                ),
                request,
            )
        elif request.operation == "close_epic":
            self.write_command(
                (
                    "gh",
                    "issue",
                    "close",
                    str(request.epic),
                    "--repo",
                    repo,
                    "--reason",
                    "completed",
                ),
                request,
            )
        elif request.operation == "update_epic":
            self.write_json(
                (
                    "gh",
                    "api",
                    f"repos/{repo}/issues/{request.epic}/comments",
                    "--method",
                    "POST",
                    "-f",
                    f"body={self.final_comment(request)}",
                ),
                request,
            )
        else:
            raise delivery.OutcomeUnknown
        state, receipt = self.reconcile(request)
        if state != "APPLIED" or not receipt:
            raise delivery.OutcomeUnknown
        return receipt

    def reconcile(self, request: Any) -> tuple[str, dict[str, object] | None]:
        if not isinstance(request, final.Request) or not final.valid_request(request):
            return "UNKNOWN", None
        repo = request.repository
        try:
            if request.operation == "create_roadmap_pr":
                pages = self.json(
                    (
                        "gh",
                        "api",
                        "--paginate",
                        "--slurp",
                        f"repos/{repo}/pulls?state=all&head={repo.split('/')[0]}:{request.roadmap_ref}&base=main&per_page=100",
                    )
                )
                if not isinstance(pages, list) or not all(
                    isinstance(page, list) for page in pages
                ):
                    return "UNKNOWN", None
                matches = []
                for page in pages:
                    for value in page:
                        if not isinstance(value, dict):
                            return "UNKNOWN", None
                        number = value.get("number")
                        if type(number) is not int or number <= 0:
                            return "UNKNOWN", None
                        bound = replace(
                            request, pr=number, operation="merge_roadmap_pr"
                        )
                        # Every returned candidate must be structurally verified.
                        info = self.pull_request(bound)
                        if info.get("state") == "OPEN":
                            matches.append(number)
                        else:
                            return "UNKNOWN", None
                if not matches:
                    return "NOT_APPLIED", None
                if len(matches) == 1:
                    return "APPLIED", {"pr": matches[0]}
                return "UNKNOWN", None
            if request.operation == "merge_roadmap_pr":
                info = self.pull_request(request)
                merge = info.get("mergeCommit")
                sha = merge.get("oid") if isinstance(merge, dict) else None
                if (
                    info.get("state") == "MERGED"
                    and isinstance(sha, str)
                    and delivery.SHA_RE.fullmatch(sha)
                ):
                    return "APPLIED", {"merge_sha": sha}
                if info.get("state") == "OPEN" and merge is None:
                    return "NOT_APPLIED", None
            elif request.operation == "close_epic":
                issue = self.json(
                    (
                        "gh",
                        "issue",
                        "view",
                        str(request.epic),
                        "--repo",
                        repo,
                        "--json",
                        "number,state",
                    )
                )
                if not isinstance(issue, dict) or issue.get("number") != request.epic:
                    return "UNKNOWN", None
                if issue.get("state") == "CLOSED":
                    return "APPLIED", {
                        "epic": request.epic,
                        "merge_sha": request.merge_sha,
                    }
                if issue.get("state") == "OPEN":
                    return "NOT_APPLIED", None
            elif request.operation == "update_epic":
                pages = self.json(
                    (
                        "gh",
                        "api",
                        "--paginate",
                        "--slurp",
                        f"repos/{repo}/issues/{request.epic}/comments?per_page=100",
                    )
                )
                if not isinstance(pages, list) or not all(
                    isinstance(page, list) for page in pages
                ):
                    return "UNKNOWN", None
                comment_matches = []
                for page in pages:
                    for item in page:
                        if not isinstance(item, dict) or not isinstance(
                            item.get("body"), str
                        ):
                            return "UNKNOWN", None
                        if item["body"] == self.final_comment(request):
                            comment_matches.append(item)
                if len(comment_matches) == 1:
                    return "APPLIED", {
                        "epic": request.epic,
                        "merge_sha": request.merge_sha,
                    }
                if not comment_matches:
                    return "NOT_APPLIED", None
        except Exception:
            return "UNKNOWN", None
        return "UNKNOWN", None
