# PR Pipelines — Security Gates

These pipelines **validate** each microservice on pull requests. They **build the image but do NOT push** to ACR (publishing lives in the non-`PR-` pipelines). Every check is **fail-closed** — a finding fails the build and blocks the merge.

## Pipelines

| Pipeline | Service | Trigger path | Language |
|----------|---------|--------------|----------|
| `PR-results-pipelines.yml` | result | `result/*` | Node.js |
| `PR-vote-app-pipelines.yml` | vote | `vote/*` | Python |
| `PR-worker-pipelines.yml` | worker | `worker/*` | .NET/C# |

## What each run does (in order)

1. **Gitleaks** — secret scan on source (fail fast, before build).
2. **SAST** — static code analysis:
   - Python (vote): **Bandit** (`-ll -ii`, medium+ severity/confidence)
   - Node.js (result): **Semgrep** (`p/javascript` ruleset)
   - .NET (worker): **Semgrep** (`p/csharp` ruleset)
3. **Trivy config** — Dockerfile misconfiguration scan (HIGH/CRITICAL).
4. **Docker build** — build the image (no push).
5. **Trivy image** — CVE scan of the built image (HIGH/CRITICAL, `--ignore-unfixed`).

> Order is fail-fast: cheap source scans run before the multi-minute image build.

## The security story

| Layer | Where | Controls |
|-------|-------|----------|
| **PR (pre-merge)** | these pipelines | Gitleaks · Bandit/Semgrep · Trivy (config + image) |
| **Publish (build → push)** | `*-pipelines.yml` | Trivy gate · Cosign signing · Syft SBOM |
| **Runtime** | cluster | Kyverno verifies signatures |

## Setup note

Azure Repos ignores YAML `pr:` triggers — each pipeline must be wired as a **Build Validation branch policy** on `main` (with its path filter) for it to gate PRs.
