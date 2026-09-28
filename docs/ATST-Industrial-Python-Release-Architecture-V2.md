# ATST Industrial Python Release Architecture V2

> Status: implementation candidate on `fix/python-release-pipeline` / Draft PR #8  
> Scope: Python package build, verification, PyPI publication, GitHub Release publication, optional Docker publication  
> Date: 2026-09-28

## 1. Executive conclusion

The release subsystem can reach an industrial-grade level, but "industrial-grade" is not defined as
"the workflow file looks complete". It means the release path is deterministic, fail-closed,
recoverable, auditable, version-consistent, supply-chain constrained, and does not allow an
unrelated publication surface to silently weaken or block the canonical Python package release.

After three review/fix rounds, the code-level architecture now has the following properties:

- one canonical package version source: `atst/_version.py`;
- tag/version identity is checked before building;
- a release tag must be reachable from `main`;
- deterministic merge gates are rerun on the exact tagged source;
- one canonical wheel and one canonical sdist are built once and reused;
- the same wheel is installed on Linux/macOS/Windows and Python 3.10-3.14;
- `atst[all]` is installed on all three operating systems on Python 3.14;
- the canonical sdist must rebuild a working wheel;
- SHA-256 checksums are generated and attached to the GitHub Release;
- GitHub Release stays Draft until required publication surfaces succeed;
- PyPI retry is idempotent and hash-verified;
- an already-published GitHub Release is treated as a verified no-op only when its canonical
  artifacts and checksum manifest are identical;
- public PyPI publication is explicitly gated by `PUBLIC_RELEASE=true`;
- Docker publication is independently gated by `PUBLISH_DOCKER=true`;
- prerelease/dev/post/local versions cannot move stable Docker channels;
- release workflow third-party Actions are pinned to immutable commit SHAs.

The remaining blocker is external verification evidence: current GitHub Actions runs on PR #8 are
failing before runner assignment (`runner_id=0`, `steps=[]`). Therefore this branch must not be
declared release-ready or merged until at least one exact-head CI run actually assigns runners and
executes the gates.

## 2. Industrial-grade acceptance criteria

A release is considered valid only if all mandatory invariants below hold.

| Domain | Invariant |
|---|---|
| Source identity | Tag version == canonical source version |
| Source provenance | Tagged commit is reachable from `main` |
| Quality | Deterministic gates pass again on exact release source |
| Versioning | `atst/_version.py` is the only version declaration |
| Build | Exactly one universal wheel + one sdist |
| Wheel | Filename and metadata match canonical version |
| sdist | Metadata and runtime closure match canonical version |
| Runtime closure | Every repository-owned Python runtime module ships in wheel + sdist |
| Typing | `atst/py.typed` ships in wheel + sdist |
| Compatibility | Wheel installs on 3 OS x Python 3.10-3.14 |
| Extras | `atst[all]` resolves/imports on 3 OS on Python 3.14 |
| Source installability | sdist can rebuild a wheel |
| Integrity | GitHub assets carry SHA-256 manifest |
| PyPI | Trusted Publishing only; no local password/token publishing path |
| Retry | Existing PyPI version must match exact local SHA-256 or fail closed |
| GitHub Release | Draft before external writes; publish only after required surfaces complete |
| Docker | Optional; never silently blocks Python-only release |
| Supply chain | Release Actions use immutable SHAs |
| Failure handling | No retry loop, no unbounded polling, no silent fallback |
| Auditability | Tag, commit, artifacts, hashes, CI run and Release remain traceable |

## 3. Canonical release topology

```text
Developer / Maintainer
        |
        | push vX.Y.Z
        v
+------------------------------+
| Release Source Gate          |
| - checkout full history      |
| - tag reachable from main    |
| - install .[all,dev]         |
| - make gates                 |
+------------------------------+
        |
        v
+------------------------------+
| Canonical Build              |
| - canonical version resolve  |
| - PEP 440 channel classify   |
| - build wheel + sdist once   |
| - artifact verifier          |
| - twine metadata check       |
| - SHA256SUMS                 |
+------------------------------+
        |
        +-------------------------+-------------------------+
        |                         |                         |
        v                         v                         v
  15-cell wheel smoke       all-extras smoke         sdist -> wheel
  3 OS x Py 3.10-3.14       3 OS x Py 3.14           rebuild/install
        |                         |                         |
        +-------------------------+-------------------------+
                                  |
                                  v
                        +---------------------+
                        | Draft GitHub Release|
                        | upload exact assets |
                        +---------------------+
                                  |
                  +---------------+----------------+
                  |                                |
                  v                                v
       PUBLIC_RELEASE != true            PUBLIC_RELEASE == true
       private GitHub release            PyPI state verification
                  |                                |
                  |                     absent -> OIDC publish
                  |                     same hash -> safe no-op
                  |                     mismatch -> hard fail
                  |                                |
                  |                     PUBLISH_DOCKER == true?
                  |                         | yes
                  |                         v
                  |                    Docker publish
                  |                         |
                  +-------------------------+
                                  |
                                  v
                        Publish GitHub Release
```

## 4. Release state machine

### 4.1 States

```text
SOURCE_UNVERIFIED
  -> SOURCE_VERIFIED
  -> ARTIFACT_BUILT
  -> ARTIFACT_VERIFIED
  -> MATRIX_VERIFIED
  -> RELEASE_DRAFT
  -> PYPI_COMPLETE | PYPI_SKIPPED
  -> DOCKER_COMPLETE | DOCKER_SKIPPED
  -> RELEASE_PUBLISHED
```

No transition is allowed to jump over an earlier mandatory state.

### 4.2 Retry semantics

Retries are intentionally idempotent.

#### PyPI

If the version is absent:

```text
PyPI 404 -> publish with OIDC
```

If the version exists:

```text
remote filename + SHA256 == local canonical artifacts
    -> safe success/no-op

remote artifact missing or SHA256 differs
    -> hard failure
```

The workflow never tries to overwrite an immutable PyPI version.

#### GitHub Release

If no Release exists:

```text
create Draft -> upload canonical assets
```

If Draft exists:

```text
resume Draft -> clobber with same canonical assets
```

If Published exists:

```text
download wheel + sdist + SHA256SUMS
-> verify manifest
-> compare manifest with current canonical build
-> identical: verified no-op
-> different: fail
```

This prevents a rerun from silently accepting a different artifact under the same release tag.

## 5. Three review rounds

## Round 1 - State machine and failure recovery

### Problems found

1. A previously published GitHub Release could exist before PyPI/Docker completed.
2. PyPI success followed by Docker failure made reruns unsafe because PyPI rejects duplicate
   version uploads.
3. Prerelease detection used tag punctuation; PEP 440 versions such as `1.1.0rc1` do not require a
   hyphen.
4. A fully published Release rerun failed merely because the Release was no longer Draft.

### Fixes

- switched trigger from published Release to tag push;
- create Draft GitHub Release only after artifact gates pass;
- added `scripts/check_pypi_release.py`;
- require exact PyPI SHA-256 match before treating an existing version as successful;
- use `packaging.version.Version` for channel classification;
- published Release reruns download and verify canonical assets, then exit successfully only when
  identical.

### Round 1 conclusion

State transitions are now bounded and retry-safe. There is no unbounded loop and no "retry until
green" behavior.

## Round 2 - Version, artifact and supply-chain integrity

### Problems found

1. Version existed in both `pyproject.toml` and `atst/__init__.py`.
2. A tag could point at code not merged into `main`.
3. Release could bypass deterministic CI evidence.
4. sdist only included a hard-coded `docs/releases/v1.0.0.md`.
5. release Actions were referenced through movable major-version tags.
6. post/local PEP 440 versions could incorrectly move stable Docker channels.

### Fixes

- introduced `atst/_version.py` as the version single source of truth;
- Hatch reads version dynamically through `[tool.hatch.version]`;
- `atst.__init__` only re-exports the canonical value;
- release tag must be an ancestor of `origin/main`;
- `make gates` reruns inside the release workflow;
- sdist uses `docs/releases/*.md`;
- Hatchling/build/Twine release tooling is pinned;
- release workflow Actions are pinned to immutable commit SHAs;
- only a plain final version can move stable Docker channels.

### Round 2 conclusion

Source identity, package identity and external artifact identity now share one chain of custody.

## Round 3 - Operational boundaries, no orphan paths, documentation

### Problem found

Docker publication was implicitly mandatory whenever public Python publication was enabled. Missing
DockerHub configuration could therefore strand an otherwise valid Python release after PyPI had
already been irreversibly published.

### Fix

The surfaces are now explicitly separated:

- `PUBLIC_RELEASE=true`: allow public PyPI Trusted Publishing;
- `PUBLISH_DOCKER=true`: additionally publish Docker;
- absent/false `PUBLISH_DOCKER`: Docker job is explicitly skipped and does not block the Python
  release.

### Round 3 conclusion

Python package publication is the canonical release path. Docker is an optional projection of the
same verified wheel, not a hidden prerequisite.

## 6. No-orphan / no-dead-loop audit

### 6.1 Canonical version path

```text
atst/_version.py
  -> Hatch dynamic version
  -> wheel/sdist metadata
  -> build verifier
  -> release tag verifier
  -> installed atst.__version__
```

There is no second writable version declaration.

### 6.2 Canonical artifact path

```text
python -m build
  -> dist wheel/sdist
  -> build_package.py verifier
  -> artifact upload
  -> OS/Python smoke
  -> extras smoke
  -> sdist rebuild
  -> GitHub Release
  -> PyPI (optional public)
  -> Docker (optional)
```

No downstream publication rebuilds the Python wheel.

### 6.3 Termination

The release system contains no scheduler loop or retry loop. All network operations are bounded by
job timeouts. Failure requires an explicit rerun; reruns validate external state before writing.

## 7. Configuration contract

### Repository variable: PUBLIC_RELEASE

- unset / false:
  - build and validate packages;
  - create/publish the GitHub Release in repository visibility;
  - do not publish to public PyPI;
  - do not publish Docker.

- true:
  - enable PyPI Trusted Publishing after all artifact gates.

This separation is important because the repository is private while PyPI is public.

### Repository variable: PUBLISH_DOCKER

- unset / false: Docker is not part of the required release transaction.
- true: publish Docker only after PyPI succeeds.

### PyPI environment

The GitHub Environment named `pypi` must be configured and should use PyPI Trusted Publishing.
No long-lived PyPI token is required by the workflow.

### Docker secrets

Required only when `PUBLISH_DOCKER=true`:

- `DOCKERHUB_USERNAME`
- `DOCKERHUB_TOKEN`

## 8. Release runbook

1. Update `atst/_version.py`.
2. Update changelog/release notes.
3. Merge through normal protected `main` review flow.
4. Confirm the exact `main` commit has real CI execution evidence.
5. Create and push tag `v<canonical-version>`.
6. Release workflow reruns deterministic gates on the tagged commit.
7. Verify canonical build and matrix jobs.
8. Draft Release is created automatically.
9. Public PyPI is performed only when `PUBLIC_RELEASE=true`.
10. Docker is performed only when `PUBLISH_DOCKER=true`.
11. GitHub Release becomes public only after required publication surfaces complete.

Do not manually upload an alternative wheel to the Release or rebuild a different wheel for Docker.

## 9. Failure matrix

| Failure | Expected result | Recovery |
|---|---|---|
| tag != source version | stop before build | fix version/tag |
| tag not reachable from main | stop before build | merge source first |
| deterministic gate fails | no release draft | fix source and create new tag/version as appropriate |
| build metadata mismatch | no release draft | fix package metadata |
| one OS/Python smoke fails | no release draft | fix compatibility |
| extras fail | no release draft | fix optional dependency compatibility |
| sdist rebuild fails | no release draft | fix source distribution |
| PyPI unavailable before publish | Draft remains | rerun later |
| PyPI already exact | safe no-op | continue |
| PyPI same version, different hash | hard fail | investigate; never overwrite |
| Docker disabled | explicit skip | release continues |
| Docker enabled and fails | Draft remains | fix Docker and rerun |
| GitHub Release already exact | verified no-op | none |
| GitHub Release same tag, different assets | hard fail | investigate tampering/drift |

## 10. Python compatibility policy

Current declared range is Python >=3.10, with explicit release smoke on:

- 3.10
- 3.11
- 3.12
- 3.13
- 3.14

Python 3.10 should be reviewed when its upstream support status changes; removal must be a deliberate
compatibility decision and must update classifiers, CI matrix and release smoke together.

## 11. Security and supply-chain controls

Implemented:

- OIDC Trusted Publishing for PyPI;
- no local `twine upload` shortcut;
- pinned build backend/toolchain;
- immutable release Action SHAs;
- checksum manifest;
- exact artifact reuse;
- source-main ancestry gate;
- no overwrite semantics for PyPI;
- fail-closed hash mismatch behavior.

Recommended repository settings outside source control:

- protect `main`;
- protect release tags (`v*`) with a GitHub ruleset;
- restrict who can change repository Actions/variables/environments;
- require reviewers on the `pypi` Environment when organizational policy requires human approval;
- enable repository security scanning appropriate to the organization.

These settings cannot be proven solely by repository source files and should be audited in GitHub
administration settings.

## 12. Current verification status

PR #8 remains Draft and unmerged.

Observed CI runs on this branch have failed before runner assignment with:

```text
runner_id = 0
steps = []
```

That means Ruff, mypy, pytest, package contracts and the new Python 3.14 gate did not actually
execute. This is infrastructure evidence, not source-level failure evidence.

Therefore:

- architecture/code-level review: three rounds completed;
- identified code-level release issues: repaired;
- final release-readiness certification: pending a real exact-head GitHub Actions execution.

The PR must remain Draft until runners execute the gates. A green status without executed steps must
not be accepted as evidence.

## 13. Final target

The intended steady-state release contract is:

```text
main-reviewed source
-> immutable tag
-> exact-source gates
-> build once
-> verify everywhere
-> draft release
-> idempotent external publication
-> publish release
```

This keeps the Python package path deterministic, bounded, auditable and recoverable while allowing
future publication surfaces to be added as independent projections rather than new canonical build
paths.
