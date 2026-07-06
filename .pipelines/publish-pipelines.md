# Publish Pipelines — Build, Scan, Sign, Publish

These pipelines **build → scan → push → sign** each microservice image to ACR. They run on merges to `main` (path-filtered per service). Unlike the PR gates, they **publish** the artifact — so they re-gate security on the exact image being shipped.

## Pipelines

| Pipeline | Service | Trigger path | Shape |
|----------|---------|--------------|-------|
| `Publish-vote-app-pipelines.yml` | vote | `vote/*` | Build stage (tar → artifact) + Push stage |
| `Publish-results-pipelines.yml` | result | `result/*` | Build stage (tar → artifact) + Push stage |
| `Publish-worker-pipelines.yml` | worker | `worker/*` | Single job, bare build → push |

> The three shapes differ **intentionally** (pilot — testing different approaches). Security controls are identical across all three; only the build/artifact plumbing differs.

## Security controls (every pipeline, fail-closed)

1. **Trivy image scan** — re-scans the built image for HIGH/CRITICAL **before push**. Fails the build → push never runs. (`--ignore-unfixed`, uses `.trivyignore`.)
2. **Cosign sign** — signs the pushed image **by digest** (`sha256:…`, never a tag).
3. **Syft SBOM + Cosign attest** — SPDX SBOM generated and attached as a signed attestation.
   - vote/result also publish the SBOM as a **build artifact**; worker keeps it as a **registry attestation only** (bare shape).

Tool versions are **pinned** (`trivyVersion` / `cosignVersion` / `syftVersion` vars) and installed from release binaries — not `curl | sh`.

## One-time setup (required)

Signing needs a cosign key pair. Generate locally, store in the `voting-platform-secrets` variable group:

```bash
cosign generate-key-pair          # creates cosign.key (private) + cosign.pub
```

| Variable group secret | Value | Notes |
|-----------------------|-------|-------|
| `cosignPrivateKey` | full contents of `cosign.key` | 🔒 mark as secret |
| `cosignPassword` | the password you set | 🔒 mark as secret |

- Set via **ADO UI**: Pipelines → Library → `voting-platform-secrets` → add both, lock each.
- Keep `cosign.pub` for the Kyverno verify policy. **Never commit `cosign.key`.**
- Production upgrade: swap the key-in-variable-group for **Azure Key Vault KMS** (`cosign sign --key azurekms://<vault>/<key>`) — no private key stored in ADO.

## Verify a published image

```bash
cosign verify --key cosign.pub <registry>/<repo>@sha256:<digest>
cosign verify-attestation --key cosign.pub --type spdxjson <registry>/<repo>@sha256:<digest>
```

## Security story (end to end)

| Layer | Where | Controls |
|-------|-------|----------|
| PR (pre-merge) | `PR-*-pipelines.yml` | Gitleaks · Bandit/Semgrep · Trivy config + image |
| **Publish (build → push)** | `Publish-*-pipelines.yml` | Trivy gate · Cosign sign (by digest) · Syft SBOM attest |
| Runtime | cluster | Kyverno verifies signature (uses `cosign.pub`) — *pending* |

## Pending / proposed hardening

Pin PR Trivy install + checksum-verify tools · GitHub App token for GitOps (no PAT in URL) · pin `vmImage` to `ubuntu-22.04` · ADO **environment approval** before Push · review `--ignore-unfixed` on the release gate.
