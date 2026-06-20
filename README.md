# Voting Platform Application

Multi-language microservice application for a distributed voting system.

## Components

- **vote** (Python Flask): Web frontend for casting votes
- **result** (Node.js): Web dashboard displaying live vote results
- **worker** (.NET/C#): Background service consuming votes from queue
- **redis**: In-memory cache and job queue (shared infrastructure)
- **postgres**: Persistent vote storage (shared infrastructure)

## Local Development

### Run with Docker Compose
```bash
docker compose up
```

- Vote app: http://localhost:8080
- Result app: http://localhost:8081

### Run with Docker Compose (production images)
```bash
docker compose -f docker-compose.images.yml up
```

## Build for Kubernetes

Each microservice has a multi-stage Dockerfile optimized for production:

```bash
# Build vote service
docker build -t <registry>/vote-app:latest vote/

# Build result service
docker build -t <registry>/result-app:latest result/

# Build worker service
docker build -t <registry>/worker-app:latest worker/
```

## Architecture

```
User Browser
  │
  ├─→ vote (Flask, port 5000)  ─→ redis (queue) ─→ worker (.NET)
  │                                                     │
  └─→ result (Node.js, port 5001) ←─ postgres ←─────┘
```

## Repository Structure

```
voting-platform-app/
├── vote/          (Python Flask application + Dockerfile)
├── result/        (Node.js application + Dockerfile)
├── worker/        (.NET application + Dockerfile)
├── healthchecks/  (Health check scripts for docker compose)
├── pipelines/     (Azure Pipelines CI definitions)
│   ├── azure-pipelines-vote.yml
│   ├── azure-pipelines-result.yml
│   └── azure-pipelines-worker.yml
├── scripts/       (Deployment helper scripts)
│   └── promote-image.sh
├── docker-compose.yml
└── docker-compose.images.yml
```

## Pipeline Workflow

This repo is part of a three-repo GitOps platform:

1. **voting-platform-app** (this repo) → Build & push images to ACR
2. **voting-platform-config** → Kustomize manifests & Argo CD ApplicationSets
3. **voting-platform-infra** → Terraform infrastructure definitions

When code is pushed to this repo:
1. Azure Pipelines triggers based on path (vote/, result/, worker/)
2. Builds Docker image and pushes to Azure Container Registry (ACR)
3. Updates voting-platform-config repo with new image digest
4. Argo CD syncs changes to AKS cluster

## Environment Variables

Each service requires database and cache connection details:

```env
DB_HOST=postgres
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=<from-key-vault>
REDIS_HOST=redis
REDIS_PORT=6379
```

In Kubernetes, these are injected via Workload Identity + Key Vault CSI driver (no hardcoded secrets).

## References

- Original: [dockersamples/example-voting-app](https://github.com/dockersamples/example-voting-app)
- Platform: [voting-platform-config](https://github.com/voting-app-micro/voting-platform-config)
- Infrastructure: [voting-platform-infra](https://github.com/voting-app-micro/voting-platform-infra)
