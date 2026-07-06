# because of dotnet, we always build on amd64, and target platforms in cli
# dotnet doesn't support QEMU for building or running. 
# (errors common in arm/v7 32bit) https://github.com/dotnet/dotnet-docker/issues/1537
# https://hub.docker.com/_/microsoft-dotnet
# hadolint ignore=DL3029
# to build for a different platform than your host, use --platform=<platform>
# for example, if you were on Intel (amd64) and wanted to build for ARM, you would use:
# docker buildx build --platform "linux/arm64/v8" .

# build compiles the program for the builder's local platform
FROM --platform=${BUILDPLATFORM} mcr.microsoft.com/dotnet/sdk:10.0 AS build
ARG TARGETPLATFORM
ARG TARGETARCH
ARG BUILDPLATFORM
RUN echo "I am running on $BUILDPLATFORM, building for $TARGETPLATFORM"

WORKDIR /source
COPY *.csproj .
RUN dotnet restore -a $TARGETARCH

COPY . .
RUN dotnet publish -c release -o /app -a $TARGETARCH --self-contained false --no-restore

# app image
FROM mcr.microsoft.com/dotnet/runtime:10.0
WORKDIR /app
COPY --from=build /app .
# Run as a non-root user to avoid container-escape risk (Trivy DS-0002).
# dotnet images ship a built-in non-root 'app' user since 8.0 — use it instead of useradd:
# the 10.0 images are Ubuntu-based, where UID 1000 is already taken by the 'ubuntu' user
# (useradd --uid 1000 fails with exit code 4).
USER app
ENTRYPOINT ["dotnet", "Worker.dll"]
