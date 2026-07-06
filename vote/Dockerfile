# base defines a base stage that uses the official python runtime base image
FROM python:3.13-slim AS base

# Add curl for healthcheck
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Set the application directory
WORKDIR /usr/local/app

# Install our requirements.txt. Do NOT install setuptools/wheel here — python:3.12+ images no
# longer ship them, and adding them back reintroduces vendored HIGH CVEs (jaraco.context, wheel).
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# dev defines a stage for development, where it'll watch for filesystem changes
FROM base AS dev
RUN pip install watchdog
ENV FLASK_ENV=development
CMD ["python", "app.py"]

# final defines the stage that will bundle the application for production
FROM base AS final

# Copy our code from the current folder to the working directory inside the container
COPY . .

# Make port 80 available for links and/or publish
EXPOSE 80

# Grant the Python interpreter permission to bind privileged port 80 as a non-root user, so the
# image is self-sufficient at runtime — no k8s NET_BIND_SERVICE capability and no port change
# needed (ArgoCD manifests stay untouched). Then drop to a non-root user (Trivy DS-0002).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libcap2-bin \
    && setcap 'cap_net_bind_service=+ep' "$(readlink -f "$(command -v python3)")" \
    && apt-get purge -y libcap2-bin \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --uid 1000 --create-home appuser
USER appuser

# Define our command to be run when launching the container
CMD ["gunicorn", "app:app", "-b", "0.0.0.0:80", "--log-file", "-", "--access-logfile", "-", "--workers", "4", "--keep-alive", "0"]
