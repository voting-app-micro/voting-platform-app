#!/usr/bin/env bash
# promote-image-pr.sh — GitOps promotion via automated Pull Request.
#
# Run from a CLASSIC RELEASE pipeline (Bash task) that triggers on completion of a
# Publish build. Updates the image tag in voting-platform-config's kustomize overlay
# on a new branch and raises a PR — it never commits to the config repo's main directly.
#
# Inputs (env vars — map ADO variables in the Bash task's environment):
#   GHE_TOKEN     (required, secret) GitHub PAT with repo scope on the config repo.
#   IMAGE_TAG     Image tag to promote. Defaults to $(Build.BuildNumber) of the
#                 triggering build (publish pipelines set buildNumber == image tag).
#   IMAGE_NAME    kustomize image name (votingapp-vote|-result|-worker). If unset,
#                 derived from the triggering build definition name.
#   REGISTRY      ACR login server. Defaults to $(ContainerRegistry) from the VG.
#
# Optional overrides:
#   CONFIG_ORG=voting-app-micro  CONFIG_REPO=voting-platform-config
#   BASE_BRANCH=main  OVERLAY_PATH=overlays/dev  KUSTOMIZE_VERSION=5.5.0
set -euo pipefail

# ---- resolve inputs ---------------------------------------------------------
: "${GHE_TOKEN:?GHE_TOKEN is required (map the secret in the task env)}"
IMAGE_TAG="${IMAGE_TAG:-${BUILD_BUILDNUMBER:-}}"
: "${IMAGE_TAG:?IMAGE_TAG is required (or run with a build artifact so BUILD_BUILDNUMBER exists)}"
REGISTRY="${REGISTRY:-${CONTAINERREGISTRY:-}}"
: "${REGISTRY:?REGISTRY is required (map ContainerRegistry from the variable group)}"

if [ -z "${IMAGE_NAME:-}" ]; then
  # Derive from the triggering build definition (classic release exposes it as
  # BUILD_DEFINITIONNAME). One release definition can then serve all three services.
  case "${BUILD_DEFINITIONNAME:-}" in
    *[Vv]ot*)    IMAGE_NAME="votingapp-vote" ;;
    *[Rr]esult*) IMAGE_NAME="votingapp-result" ;;
    *[Ww]orker*) IMAGE_NAME="votingapp-worker" ;;
    *) echo "ERROR: IMAGE_NAME not set and cannot derive it from BUILD_DEFINITIONNAME='${BUILD_DEFINITIONNAME:-}'"; exit 1 ;;
  esac
fi

CONFIG_ORG="${CONFIG_ORG:-voting-app-micro}"
CONFIG_REPO="${CONFIG_REPO:-voting-platform-config}"
BASE_BRANCH="${BASE_BRANCH:-main}"
OVERLAY_PATH="${OVERLAY_PATH:-overlays/dev}"
KUSTOMIZE_VERSION="${KUSTOMIZE_VERSION:-5.5.0}"
PROMOTE_BRANCH="promote/${IMAGE_NAME}-${IMAGE_TAG}"
# exported for the python JSON builder below
export IMAGE_NAME IMAGE_TAG PROMOTE_BRANCH BASE_BRANCH

echo "Promoting ${IMAGE_NAME} -> ${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}"
echo "Target: ${CONFIG_ORG}/${CONFIG_REPO} ${OVERLAY_PATH} (PR into ${BASE_BRANCH})"

# ---- tooling (pinned; hosted agents don't ship kustomize) --------------------
if ! command -v kustomize >/dev/null 2>&1; then
  echo "Installing kustomize v${KUSTOMIZE_VERSION}..."
  wget -qO kustomize.tar.gz "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv${KUSTOMIZE_VERSION}/kustomize_v${KUSTOMIZE_VERSION}_linux_amd64.tar.gz"
  tar -xzf kustomize.tar.gz kustomize
  sudo mv kustomize /usr/local/bin/ 2>/dev/null || mv kustomize "$HOME/bin/" || { mkdir -p "$HOME/bin"; mv kustomize "$HOME/bin/"; export PATH="$HOME/bin:$PATH"; }
fi

# ---- clone + branch + edit ----------------------------------------------------
# Auth via a per-command header only — the token must never be embedded in the remote
# URL, or it persists in .git/config and can surface in logs/error output.
AUTH_HEADER="AUTHORIZATION: Basic $(printf 'x-access-token:%s' "$GHE_TOKEN" | base64 -w0)"
REPO_URL="https://github.com/${CONFIG_ORG}/${CONFIG_REPO}.git"
WORKDIR="$(mktemp -d)"

git -c http.extraHeader="$AUTH_HEADER" clone --depth 1 --branch "$BASE_BRANCH" "$REPO_URL" "$WORKDIR/config"
cd "$WORKDIR/config"
git config user.email "ado-release@voting-platform.local"
git config user.name "ado-gitops-bot"
git checkout -b "$PROMOTE_BRANCH"

( cd "$OVERLAY_PATH" && kustomize edit set image "${IMAGE_NAME}=${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}" )

if git diff --quiet; then
  echo "Overlay already pins ${IMAGE_TAG} — nothing to promote. Exiting green."
  exit 0
fi

git add "${OVERLAY_PATH}/kustomization.yaml"
git commit -m "chore(gitops): promote ${IMAGE_NAME} to ${IMAGE_TAG}"

# Force-push is safe here: the branch name embeds image+tag, so it only ever
# overwrites a previous run of this same promotion (release re-runs).
git -c http.extraHeader="$AUTH_HEADER" push --force origin "$PROMOTE_BRANCH"

# ---- raise the PR -------------------------------------------------------------
API="https://api.github.com/repos/${CONFIG_ORG}/${CONFIG_REPO}"
PR_BODY=$(cat <<EOF
Automated GitOps promotion from Azure DevOps release.

| | |
|---|---|
| Image | \`${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}\` |
| Overlay | \`${OVERLAY_PATH}\` |
| Triggering build | ${BUILD_DEFINITIONNAME:-n/a} #${IMAGE_TAG} |

Merging this PR makes Argo CD roll \`${IMAGE_NAME}\` to \`${IMAGE_TAG}\`.
EOF
)

RESP=$(curl -sS -w '\n%{http_code}' -X POST "$API/pulls" \
  -H "Authorization: Bearer $GHE_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  -d "$(python3 -c 'import json,sys,os; print(json.dumps({
        "title": f"chore(gitops): promote {os.environ[\"IMAGE_NAME\"]} to {os.environ[\"IMAGE_TAG\"]}",
        "head": os.environ["PROMOTE_BRANCH"], "base": os.environ["BASE_BRANCH"],
        "body": sys.stdin.read()}))' <<<"$PR_BODY")" )
HTTP_CODE=$(tail -n1 <<<"$RESP")
BODY=$(sed '$d' <<<"$RESP")

if [ "$HTTP_CODE" = "201" ]; then
  echo "PR created: $(python3 -c 'import json,sys; print(json.load(sys.stdin)["html_url"])' <<<"$BODY")"
elif [ "$HTTP_CODE" = "422" ] && grep -q "already exists" <<<"$BODY"; then
  # Re-run of the same release: branch was force-updated, the open PR now shows the
  # refreshed commit. Find and report it, succeed.
  PR_URL=$(curl -sS -H "Authorization: Bearer $GHE_TOKEN" -H "Accept: application/vnd.github+json" \
    "$API/pulls?head=${CONFIG_ORG}:${PROMOTE_BRANCH}&state=open" \
    | python3 -c 'import json,sys; prs=json.load(sys.stdin); print(prs[0]["html_url"] if prs else "")')
  echo "PR already open (updated in place): ${PR_URL}"
else
  echo "ERROR: PR creation failed (HTTP ${HTTP_CODE}):"
  echo "$BODY"
  exit 1
fi
