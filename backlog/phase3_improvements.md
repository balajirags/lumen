# Phase 3: Native Distribution & Dependency Security

## Background

Lumen currently offers two install paths: Docker (recommended, zero prerequisites) and
native install (`make lumen-install` — requires Java 21, Node 20, Python 3.11 on the user's
machine). A third path exists via `scripts/build-native.sh` which produces self-contained
relocatable tarballs bundling JRE + Node + Python venv, but it lacks CI automation, a
one-line installer, and has dependency pinning gaps that affect supply-chain security.

The goal is to make `lumen` as easy to install as `curl | bash` on macOS and Linux, with
cryptographic verification at every layer, while keeping Docker as the zero-config default.

### Current state

| Path | Prerequisites | Reproducible? | Cross-platform? |
|---|---|---|---|
| Docker | Docker only | ✅ Digest-pinned images | ✅ Any Docker host |
| Native install | Java 21, Node 20, Python 3.11, uv | ⚠️ Lockfiles partially bypassed | ⚠️ Dev machine only |
| Native tarball | Build machine only | ⚠️ `pip install` ignores `uv.lock` | ⚠️ Manual, single-arch |

### Dependency pinning audit

| Runtime | Version Pins | Lockfile | Native Build Respects Lock? |
|---|---|---|---|
| Java/Gradle | ✅ Exact in `libs.versions.toml` | ❌ No `gradle.lockfile` | N/A |
| Node/npm | ⚠️ Ranges (`~`, `^`) | ⚠️ Exists but gitignored | ⚠️ `install.sh` uses `npm install` not `npm ci` |
| Python pipeline | ❌ Loose `>=` ranges | ✅ `uv.lock` versioned | ❌ `build-native.sh` uses bare `pip` |
| Python parser | ❌ Loose `>=` ranges | ❌ No lockfile | ❌ |

---

## Phase 0: Dependency Security Hardening

**Goal:** Ensure native tarballs are built from locked, verified, reproducible dependencies.

- [x] 0a. Un-gitignore `indexer/parsers/javascript/package-lock.json` and commit it
- [x] 0b. Change `npm install` → `npm ci` in `indexer/install.sh`
- [x] 0c. Change `pip install` → `uv pip install --frozen` in `scripts/build-native.sh`
- [x] 0d. Add `distributionSha256Sum` to `indexer/gradle/wrapper/gradle-wrapper.properties`
- [x] 0e. Add upper bounds to Python dependency ranges in `pipeline/pyproject.toml` and `indexer/parsers/python/requirements.txt`
- [x] 0f. Create `.github/dependabot.yml` covering npm, pip, gradle, and GitHub Actions ecosystems

**Files:**
- `.gitignore` — remove package-lock.json ignore rule
- `indexer/install.sh` — npm install → npm ci
- `scripts/build-native.sh` — pip → uv pip install --frozen
- `indexer/gradle/wrapper/gradle-wrapper.properties` — add SHA256
- `pipeline/pyproject.toml` — add upper bounds (e.g., `click>=8.1,<9.0`)
- `indexer/parsers/python/requirements.txt` — add upper bounds
- `.github/dependabot.yml` — new file

---

## Phase 1: Harden build-native.sh + Makefile Integration

**Goal:** Make the existing script more robust and accessible.

- [x] 1a. Add `lumen-native-build` Makefile target invoking `scripts/build-native.sh`
- [x] 1b. Extract VERSION from `pipeline/pyproject.toml` instead of hardcoding in `build-native.sh`
- [x] 1c. Add `--version` flag to `build-native.sh` for CI override (tag-based)
- [x] 1d. Generate `SHA256SUMS` manifest inside the tarball covering critical binaries (JAR, node, plantuml.jar, kuzu .so/.node)
- [x] 1e. Add `verify.sh` inside the bundle for post-install integrity checking

**Files:**
- `scripts/build-native.sh` — version extraction, internal checksums, `--version` flag
- `Makefile` — add `lumen-native-build` target

---

## Phase 2: GitHub Actions CI for Cross-Platform Builds

**Goal:** Automated release artifacts for 4 platforms on every git tag.

- [x] 2a. Create `.github/workflows/release.yml` triggered on `v*` tags
- [x] 2b. Build matrix: macOS arm64 (`macos-latest`), macOS amd64 (`macos-13`), Linux amd64 (`ubuntu-latest`), Linux arm64 (QEMU)
- [x] 2c. Each job: install Java 21 + Node 20 + Python 3.11 → run `build-native.sh --version $TAG`
- [x] 2d. Aggregate tarballs + SHA256 files into GitHub Release with `CHECKSUMS.txt`

**Files:**
- `.github/workflows/release.yml` — new workflow
- `scripts/build-native.sh` — consumes `--version` flag from Phase 1

**Linux arm64 note:** GitHub Actions lacks free native arm64 Linux runners. Start with QEMU
emulation (slow but works). Fall back to shipping 3 platforms if build time is unacceptable.
Cross-compilation is not viable because `npm ci` builds the native KuzuDB addon for the host arch.

---

## Phase 3: Secure One-Line Installer

**Goal:** `curl -fsSL https://raw.githubusercontent.com/<owner>/lumen/main/scripts/install-lumen.sh | bash`

- [x] 3a. Create `scripts/install-lumen.sh` that:
  - Detects OS + arch via `uname`
  - Resolves latest release tag from GitHub API
  - Downloads correct tarball + `.sha256` sidecar from GitHub Releases
  - Verifies SHA256 before extraction — aborts on mismatch
  - Extracts to `~/.local/share/lumen/` (or `$LUMEN_INSTALL_DIR`)
  - Symlinks `lumen` into `~/.local/bin`
  - Warns if graphviz is missing, prints PATH instructions if needed
- [x] 3b. Security hardening: HTTPS only, no `eval`, `set -euo pipefail`, support `VERSION=v1.2.0` pinning, print actions before executing
- [x] 3c. Update `README.md` with one-line install command

**Files:**
- `scripts/install-lumen.sh` — new installer script
- `README.md` — updated install section

---

## Verification

1. Run `make lumen-native-build` locally → tarball extracts and `./lumen run --help` works
2. Confirm version in tarball name matches `pyproject.toml`
3. Extract tarball, run `./verify.sh` → all internal checksums pass
4. Push `v0.1.0-rc1` tag → CI produces 3–4 platform tarballs in draft GitHub Release
5. `curl -fsSL ... | bash` on clean macOS → `~/.local/bin/lumen` works, graphviz warning shown
6. Same test in `docker run ubuntu:22.04` → validates Linux path
7. Modify tarball bytes, re-run installer → SHA256 check aborts

---

## Decisions

- Releases hosted on GitHub (`git@github.com:<owner-name>/lumen.git`)
- Install location: `~/.local/share/lumen/` (XDG-compliant, no sudo)
- No Homebrew tap yet — GitHub Releases + installer covers the need; formula can wrap tarball later
- No Windows native — Linux tarball works in WSL2; documented as the Windows path
- Graphviz stays an external system dependency (brew/apt)
- No code signing / notarization (future — requires Apple Developer account)

---

## Phase 4: Docker Image on GitHub Container Registry (GHCR)

**Goal:** Push multi-arch Docker image to GHCR on every release tag so users can `docker pull ghcr.io/<owner>/lumen:v0.2.0`.

- [ ] 4a. Add `docker-image` job to `.github/workflows/release.yml` that:
  - Depends on `validate-version` (reuse existing version guard)
  - Uses `docker/setup-buildx-action` for multi-platform builds
  - Uses `docker/login-action` to authenticate to `ghcr.io` via `GITHUB_TOKEN`
  - Uses `docker/build-push-action` to build + push from existing `Dockerfile`
  - Targets `linux/amd64` and `linux/arm64` platforms
  - Tags: `ghcr.io/<owner>/lumen:vX.Y.Z`, `ghcr.io/<owner>/lumen:latest`
- [ ] 4b. Update `README.md` Docker section to reference GHCR pull command
- [ ] 4c. Update `lumen-docker-run.sh` to default `DOCKER_IMAGE` to GHCR image if no local build exists

**Files:**
- `.github/workflows/release.yml` — add `docker-image` job
- `README.md` — add GHCR pull instructions
- `scripts/lumen-docker-run.sh` — optional GHCR fallback

**Notes:**
- GHCR is free for public repos, no separate Docker Hub account needed
- Multi-arch via buildx avoids shipping a 500+ MB tar in the GitHub Release
- Existing `lumen-docker-release.sh` (local tar export) stays for air-gapped environments
- `GITHUB_TOKEN` has `packages: write` permission — no secrets to configure
- No auto-update mechanism (re-run installer to update)
