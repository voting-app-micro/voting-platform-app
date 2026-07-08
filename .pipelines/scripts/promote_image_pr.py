#!/usr/bin/env python3
"""GitOps promotion via automated Pull Request.

Run from a classic RELEASE pipeline (Python/Bash task) that triggers on completion
of a Publish build. Updates the image tag in voting-platform-config's kustomize
overlay on a promote/<image>-<tag> branch and raises a PR — it never commits to
the config repo's main directly.

Inputs (env vars — map ADO variables in the task's environment):
  GHE_TOKEN    (required, secret) GitHub PAT with repo scope on the config repo.
  IMAGE_TAG    Image tag to promote. Defaults to BUILD_BUILDNUMBER of the
               triggering build (publish pipelines set buildNumber == image tag).
  IMAGE_NAME   kustomize image name (votingapp-vote|-result|-worker). If unset,
               derived from BUILD_DEFINITIONNAME of the triggering build.
  REGISTRY     ACR login server. Defaults to CONTAINERREGISTRY (VG variable).

Optional overrides (defaults in CONFIG below): CONFIG_ORG, CONFIG_REPO,
  BASE_BRANCH, OVERLAY_PATH.
  DRY_RUN=1    do everything except push the branch / open the PR (local testing).
"""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

CONFIG = {
    "CONFIG_ORG": "voting-app-micro",
    "CONFIG_REPO": "voting-platform-config",
    "BASE_BRANCH": "main",
    "OVERLAY_PATH": "overlays/dev",
}


def die(msg: str) -> None:
    print(f"##[error]{msg}")  # ##[error] renders red in ADO logs
    sys.exit(1)


def run(args: list[str], cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a command, echoing it with the auth header masked."""
    shown = ["***" if a.startswith("http.extraHeader=") else a for a in args]
    print(f"$ {' '.join(shown)}")
    return subprocess.run(args, cwd=cwd, check=check, text=True, capture_output=False)


def resolve_inputs() -> dict:
    # CLI args take precedence over env vars. --token exists because the classic
    # PythonScript task has no env-mapping UI and ADO never auto-exposes secrets:
    # pass `--token $(gitOpsPAT)` in the task's Arguments (masked in logs).
    cli = argparse.ArgumentParser()
    for flag in ("--token", "--tag", "--image", "--registry"):
        cli.add_argument(flag)
    args, _ = cli.parse_known_args()

    env = os.environ
    token = args.token or env.get("GHE_TOKEN") or die(
        "GitHub token is required: pass --token $(gitOpsPAT) or set GHE_TOKEN")
    tag = args.tag or env.get("IMAGE_TAG") or env.get("BUILD_BUILDNUMBER") or die(
        "IMAGE_TAG is required (or trigger from a build so BUILD_BUILDNUMBER exists)")
    registry = args.registry or env.get("REGISTRY") or env.get("CONTAINERREGISTRY") or die(
        "REGISTRY is required (link the variable group so ContainerRegistry flows in)")

    image = args.image or env.get("IMAGE_NAME")
    if not image:
        # Derive from the triggering build definition so one release definition can
        # serve all three services.
        definition = env.get("BUILD_DEFINITIONNAME", "")
        for pattern, name in ((r"vot", "votingapp-vote"),
                              (r"result", "votingapp-result"),
                              (r"worker", "votingapp-worker")):
            if re.search(pattern, definition, re.IGNORECASE):
                image = name
                break
        else:
            die(f"IMAGE_NAME not set and cannot derive it from BUILD_DEFINITIONNAME={definition!r}")

    cfg = {k: env.get(k, v) for k, v in CONFIG.items()}
    return {
        **cfg,
        "token": token,
        "image": image,
        "tag": tag,
        "registry": registry,
        "branch": f"promote/{image}-{tag}",
        "dry_run": env.get("DRY_RUN", "").lower() in ("1", "true", "yes"),
    }


def git_auth_flag(token: str) -> list[str]:
    """Per-command auth header — the token must never be embedded in the remote URL,
    or it persists in .git/config and can surface in logs/error output."""
    b64 = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return ["-c", f"http.extraHeader=AUTHORIZATION: Basic {b64}"]


def update_kustomization(path: str, image: str, registry: str, tag: str) -> bool:
    """Pin image -> registry/image:tag in the overlay's images: block, preserving
    all comments/formatting. Returns False if the file already pins this version.

    Line-based edit on purpose: a YAML round-trip (pyyaml) would strip the file's
    comment blocks, and the images: entries have a fixed, kustomize-managed shape.
    """
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    out, changed, found, in_entry = [], False, False, False
    for line in lines:
        if re.match(rf"^\s*-\s*name:\s*{re.escape(image)}\s*$", line):
            in_entry, found = True, True
        elif re.match(r"^\s*-\s*name:", line):
            in_entry = False
        elif in_entry:
            for key, value in (("newName", f"{registry}/{image}"), ("newTag", f'"{tag}"')):
                m = re.match(rf"^(\s*{key}:\s*)(\S+)\s*$", line)
                if m and m.group(2).strip('"') != value.strip('"'):
                    line = f"{m.group(1)}{value}\n"
                    changed = True
        out.append(line)

    if not found:
        die(f"no images entry named {image!r} in {path} — add it to the overlay first")
    if changed:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.writelines(out)
    return changed


def github_api(method: str, url: str, token: str, payload: dict | None = None) -> tuple[int, dict | list]:
    req = urllib.request.Request(
        url, method=method,
        data=json.dumps(payload).encode() if payload else None,
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "User-Agent": "ado-gitops-promotion"})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def main() -> None:
    p = resolve_inputs()
    target = f"{p['registry']}/{p['image']}:{p['tag']}"
    print(f"Promoting {p['image']} -> {target}")
    print(f"Target: {p['CONFIG_ORG']}/{p['CONFIG_REPO']} {p['OVERLAY_PATH']} (PR into {p['BASE_BRANCH']})")

    repo_url = f"https://github.com/{p['CONFIG_ORG']}/{p['CONFIG_REPO']}.git"
    auth = git_auth_flag(p["token"])
    workdir = os.path.join(tempfile.mkdtemp(prefix="promote-"), "config")

    run(["git", *auth, "clone", "--depth", "1", "--branch", p["BASE_BRANCH"], repo_url, workdir])
    run(["git", "config", "user.email", "ado-release@voting-platform.local"], cwd=workdir)
    run(["git", "config", "user.name", "ado-gitops-bot"], cwd=workdir)
    run(["git", "checkout", "-b", p["branch"]], cwd=workdir)

    kfile = os.path.join(workdir, p["OVERLAY_PATH"], "kustomization.yaml")
    if not update_kustomization(kfile, p["image"], p["registry"], p["tag"]):
        print(f"Overlay already pins {p['tag']} — nothing to promote. Exiting green.")
        return

    run(["git", "add", os.path.join(p["OVERLAY_PATH"], "kustomization.yaml")], cwd=workdir)
    run(["git", "commit", "-m", f"chore(gitops): promote {p['image']} to {p['tag']}"], cwd=workdir)

    if p["dry_run"]:
        print("\nDRY_RUN — diff that would be pushed:")
        run(["git", "show", "--stat", "--patch", "HEAD"], cwd=workdir)
        print("DRY_RUN — skipping push and PR creation.")
        return

    # Force-push is safe: the branch name embeds image+tag, so it only ever
    # overwrites a previous run of this same promotion (release re-runs).
    run(["git", *auth, "push", "--force", "origin", p["branch"]], cwd=workdir)

    api = f"https://api.github.com/repos/{p['CONFIG_ORG']}/{p['CONFIG_REPO']}"
    body = (f"Automated GitOps promotion from Azure DevOps release.\n\n"
            f"| | |\n|---|---|\n"
            f"| Image | `{target}` |\n"
            f"| Overlay | `{p['OVERLAY_PATH']}` |\n"
            f"| Triggering build | {os.environ.get('BUILD_DEFINITIONNAME', 'n/a')} #{p['tag']} |\n\n"
            f"Merging this PR makes Argo CD roll `{p['image']}` to `{p['tag']}`.")
    status, resp = github_api("POST", f"{api}/pulls", p["token"], {
        "title": f"chore(gitops): promote {p['image']} to {p['tag']}",
        "head": p["branch"], "base": p["BASE_BRANCH"], "body": body})

    if status == 201:
        print(f"PR created: {resp['html_url']}")
    elif status == 422 and "already exist" in json.dumps(resp):
        # Re-run of the same release: the branch was force-updated, the open PR now
        # shows the refreshed commit. Find and report it, succeed.
        status, prs = github_api(
            "GET", f"{api}/pulls?head={p['CONFIG_ORG']}:{p['branch']}&state=open", p["token"])
        url = prs[0]["html_url"] if status == 200 and prs else "(existing PR not found)"
        print(f"PR already open (updated in place): {url}")
    else:
        die(f"PR creation failed (HTTP {status}): {json.dumps(resp)[:500]}")


if __name__ == "__main__":
    main()
