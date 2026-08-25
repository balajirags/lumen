# Docker Sources

These sources were used to review Lumen's Docker base images, maintenance posture, and distribution security assumptions.

- Python Official Image: https://hub.docker.com/_/python/
  - Reference for `python:3.11-slim`.

- Node Official Image: https://hub.docker.com/_/node
  - Reference for `node:20-slim`.

- Eclipse Temurin Official Image: https://hub.docker.com/_/eclipse-temurin
  - Reference for `eclipse-temurin:21-jdk-jammy`.

- Adoptium Supported Platforms: https://adoptium.net/supported-platforms/
  - Reference for Temurin platform support and maintenance context.

- Ubuntu Release Cycle: https://ubuntu.com/about/release-cycle
  - Reference for Ubuntu support lifecycle when evaluating the Jammy-based Java builder image.

Current Dockerfiles pin their base/helper images by digest.
