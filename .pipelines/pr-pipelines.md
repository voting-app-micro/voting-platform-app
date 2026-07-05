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
   - Python (vote): **Bandit** (`-lll -ii`, HIGH severity only)
   - Node.js (result): **Semgrep** (`p/javascript`, `--severity ERROR`)
   - .NET (worker): **Semgrep** (`p/csharp`, `--severity ERROR`)
3. **Trivy config** — Dockerfile misconfiguration scan (HIGH/CRITICAL).
4. **Docker build** — build the image (no push).
5. **Trivy image** — CVE scan of the built image (HIGH/CRITICAL, `--ignore-unfixed`).

> Order is fail-fast: cheap source scans run before the multi-minute image build.
>
> **Gate threshold:** only **HIGH/CRITICAL** findings fail the build (SAST + Trivy). Lower-severity findings are reported but non-blocking.

## The security story

| Layer | Where | Controls |
|-------|-------|----------|
| **PR (pre-merge)** | these pipelines | Gitleaks · Bandit/Semgrep · Trivy (config + image) |
| **Publish (build → push)** | `*-pipelines.yml` | Trivy gate · Cosign signing · Syft SBOM |
| **Runtime** | cluster | Kyverno verifies signatures |

## Runtime note — non-root + privileged port

Trivy `DS-0002` requires images to run as **non-root** (`USER`). The **vote** and **result** containers bind **port 80** (privileged, <1024), which a non-root user can't bind by default.

Chosen fix: **`setcap 'cap_net_bind_service=+ep'`** on the interpreter (`node` / `python3`) inside each Dockerfile. The image can then bind port 80 as a non-root user **on its own** — no k8s `securityContext` capability, no port change, no Service/probe edits. This keeps the **ArgoCD-managed manifests untouched**, so nothing needs to be coordinated at rollout. **worker** binds no port and needs neither `setcap` nor a capability.

> Smoke-test once in a non-prod env (`docker run` + confirm it serves on :80 as the non-root user) before relying on it in prod — `setcap` on an interpreter is the one runtime behavior CI can't verify (pipelines only build, never run).

## Setup note

Azure Repos ignores YAML `pr:` triggers — each pipeline must be wired as a **Build Validation branch policy** on `main` (with its path filter) for it to gate PRs.
