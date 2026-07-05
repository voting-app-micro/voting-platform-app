FROM node:8.9-slim

RUN apt-get update -qq && apt-get install -qy \ 
    ca-certificates \
    bzip2 \
    curl \
    libfontconfig \
    --no-install-recommends
RUN yarn global add phantomjs-prebuilt
ADD . /app
WORKDIR /app
# Non-root (Trivy DS-0002); test tooling only, no port binding so no capability needed.
USER node
CMD ["/app/tests.sh"]
