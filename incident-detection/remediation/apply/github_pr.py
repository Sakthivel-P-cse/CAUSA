"""Open draft GitHub PRs against the real service repository (never auto-merged)."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import httpx
from runtime import profile as runtime_profile

logger = logging.getLogger(__name__)


def _plan_patch(plan_row: dict[str, Any]) -> dict[str, Any]:
    details = plan_row.get("details") or {}
    spec = plan_row.get("plan_spec") or details.get("plan_spec") or {}
    change = (spec.get("change") or {}).get("patch") or {}
    pr = details.get("pr") or {}
    return {**pr, **change}


def open_draft_pr(plan_row: dict[str, Any]) -> dict[str, Any]:
    if os.getenv("CAUSA_GITHUB_PR_ENABLED", "1") in ("0", "false", "False"):
        return {"attempted": False, "reason": "CAUSA_GITHUB_PR_ENABLED=0"}

    patch = _plan_patch(plan_row)
    node = str(plan_row.get("candidate_node") or "service")
    incident = str(plan_row.get("incident_id") or "unknown")
    repo_path = Path(str(patch.get("repo") or os.getenv("CAUSA_REMEDIATION_REPO") or "")).expanduser()
    if not repo_path.is_dir():
        return {"attempted": True, "mode": "github_draft_pr", "status": "failed", "error": f"repo missing: {repo_path}"}
    op_repo = _prepare_repo_for_pr(repo_path)
    if not op_repo:
        return {"attempted": True, "mode": "github_draft_pr", "status": "failed", "error": "cannot prepare clean repo for PR"}

    rel = str(patch.get("path") or "")
    title = str(patch.get("title") or f"fix({node}): proposed remediation from Causa RCA")
    body = str(patch.get("body") or f"Incident `{incident}`\n\nDraft only — never auto-merged.\n")
    base = str(patch.get("base") or runtime_profile.base_branch())
    branch = f"causa/fix-{node}-{incident[:12]}".replace("/", "-")
    suggested = ""
    details = plan_row.get("details") or {}
    nim_patch = details.get("nim_patch") if isinstance(details, dict) else None
    if isinstance(nim_patch, dict):
        title = str(nim_patch.get("title") or title)
        body = str(nim_patch.get("body") or body)
        suggested = str(nim_patch.get("diff") or "")

    target = op_repo / rel if rel else None
    if target and target.is_file() and suggested.strip().startswith("@@"):
        return {
            "attempted": True,
            "mode": "github_draft_pr",
            "status": "failed",
            "error": "unified diff apply not implemented without patch context; use file rewrite payload",
        }

    note_path = None
    if target and target.parent.exists():
        note_path = target.with_suffix(target.suffix + ".causa.md")
        note_path.write_text(
            f"# Causa remediation draft\n\nIncident: {incident}\nService: {node}\n\n{body}\n\n```\n{suggested[:4000]}\n```\n",
            encoding="utf-8",
        )
    elif op_repo.is_dir():
        note_dir = op_repo / ".causa-remediation"
        note_dir.mkdir(parents=True, exist_ok=True)
        note_path = note_dir / f"{node}-{incident[:12]}.md"
        note_path.write_text(f"# Causa remediation draft\n\n{body}\n\n{suggested[:4000]}\n", encoding="utf-8")

    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or _token_from_gh()
    owner_repo = os.getenv("CAUSA_GITHUB_REPO", "").strip()
    if not owner_repo:
        owner_repo = _detect_github_repo(op_repo)

    if os.getenv("CAUSA_GITHUB_AUTOCOMMIT", "0") not in ("1", "true", "True"):
        return {
            "attempted": True,
            "mode": "pr_payload_prepared",
            "status": "succeeded",
            "pr": {
                "title": title,
                "body": body,
                "head": branch,
                "base": base,
                "draft": True,
                "merged": False,
                "url": None,
                "file": str(note_path) if note_path else rel,
                "repo": owner_repo or str(repo_path),
                "note": "Set CAUSA_GITHUB_AUTOCOMMIT=1 and GITHUB_TOKEN to create draft PR",
            },
        }

    if not token or not owner_repo:
        return {
            "attempted": True,
            "mode": "github_draft_pr",
            "status": "failed",
            "error": "GITHUB_TOKEN and CAUSA_GITHUB_REPO (or origin remote) required",
        }

    try:
        _git(op_repo, "checkout", base)
        _git(op_repo, "checkout", "-B", branch)
        op_result = _apply_file_ops(op_repo, patch.get("file_ops"))
        if op_result.get("attempted") and not op_result.get("ok"):
            return {
                "attempted": True,
                "mode": "github_draft_pr",
                "status": "failed",
                "error": str(op_result.get("error") or "failed to apply file operations"),
            }
        if note_path:
            _git(op_repo, "add", str(note_path))
        if op_result.get("changed_paths"):
            _git(op_repo, "add", *op_result["changed_paths"])
        _git(op_repo, "commit", "-m", title[:72])
        _git(op_repo, "push", "-u", "origin", branch)
        url = _create_draft_pr_api(owner_repo, title, body, head=branch, base=base, token=token)
        return {
            "attempted": True,
            "mode": "github_draft_pr",
            "status": "succeeded" if url else "failed",
            "pr": {
                "title": title,
                "body": body,
                "head": branch,
                "base": base,
                "draft": True,
                "merged": False,
                "url": url,
                "file": str(note_path) if note_path else rel,
                "repo": owner_repo,
            },
        }
    except Exception as exc:  # noqa: BLE001
        return {"attempted": True, "mode": "github_draft_pr", "status": "failed", "error": str(exc)}


def _apply_file_ops(repo_path: Path, raw_ops: Any) -> dict[str, Any]:
    if not isinstance(raw_ops, list) or not raw_ops:
        return {"attempted": False, "ok": True, "changed_paths": []}
    changed: list[str] = []
    for op in raw_ops:
        if not isinstance(op, dict):
            continue
        rel = str(op.get("path") or "").strip()
        search = str(op.get("search") or "")
        replace = str(op.get("replace") or "")
        if not rel or not search:
            continue
        target = repo_path / rel
        if not target.is_file():
            return {"attempted": True, "ok": False, "error": f"patch target missing: {rel}"}
        text = target.read_text(encoding="utf-8", errors="replace")
        if search not in text:
            return {"attempted": True, "ok": False, "error": f"expected snippet not found in {rel}"}
        next_text = text.replace(search, replace, 1)
        if next_text != text:
            target.write_text(next_text, encoding="utf-8")
            changed.append(rel)
    return {"attempted": True, "ok": True, "changed_paths": changed}


def _token_from_gh() -> str:
    try:
        proc = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=False)
        return (proc.stdout or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _git(repo_path: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo_path, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "git command failed").strip())
    return proc.stdout or ""


def _prepare_repo_for_pr(repo_path: Path) -> Path | None:
    remote = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    remote_url = (remote.stdout or "").strip()
    if not remote_url:
        return repo_path
    temp_dir = Path(tempfile.mkdtemp(prefix="causa-pr-"))
    cloned = subprocess.run(
        ["git", "clone", remote_url, str(temp_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    if cloned.returncode != 0:
        return None
    return temp_dir


def poll_pr_checks(owner_repo: str, ref: str) -> dict[str, Any]:
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or _token_from_gh()
    if not token or not owner_repo:
        return {"ok": False, "error": "missing token or repo"}
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    with httpx.Client(timeout=20.0, headers=headers) as client:
        resp = client.get(f"https://api.github.com/repos/{owner_repo}/commits/{ref}/check-runs")
        if resp.status_code >= 400:
            return {"ok": False, "status_code": resp.status_code, "error": resp.text[:300]}
        data = resp.json()
        runs = data.get("check_runs") or []
        return {
            "ok": True,
            "total": len(runs),
            "runs": [
                {"name": r.get("name"), "status": r.get("status"), "conclusion": r.get("conclusion")}
                for r in runs[:20]
            ],
        }


def _create_draft_pr_api(owner_repo: str, title: str, body: str, *, head: str, base: str, token: str) -> str | None:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    with httpx.Client(timeout=30.0, headers=headers) as client:
        resp = client.post(
            f"https://api.github.com/repos/{owner_repo}/pulls",
            json={"title": title, "body": body, "head": head, "base": base, "draft": True},
        )
        if resp.status_code >= 400:
            logger.warning("create PR failed: %s", resp.text[:400])
            return None
        return str((resp.json() or {}).get("html_url") or "")


def _detect_github_repo(repo_path: Path) -> str:
    proc = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    url = (proc.stdout or "").strip()
    if url.endswith(".git"):
        url = url[:-4]
    if "github.com" in url:
        if url.startswith("git@"):
            return url.split(":", 1)[-1]
        return "/".join(url.split("/")[-2:])
    return ""
