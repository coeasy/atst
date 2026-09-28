# ATST Industrial Python Release Architecture V3

> Status: implemented on `fix/python-release-pipeline` / Draft PR #8  
> Date: 2026-09-28  
> Scope: Python build, validation, PyPI publication, GitHub Release, optional Docker projection  
> Supersedes: `ATST-Industrial-Python-Release-Architecture-V2.md`

## 1. Conclusion

The release subsystem is now designed as an industrial-grade, fail-closed publication pipeline.

"Industrial-grade" here means:

- deterministic source identity;
- reproducible artifacts;
- one canonical wheel/sdist build path;
- exact artifact reuse across all publication surfaces;
- bounded validation and retry behavior;
- explicit public/private publication boundaries;
- idempotent recovery after partial irreversible publication;
- supply-chain provenance and attestations;
- auditable release metadata;
- no hidden Docker dependency for Python publication;
- no orphan build path, no silent fallback, no unbounded retry loop;
- contract tests protecting the release architecture itself.

The remaining blocker is external execution evidence: GitHub Actions on PR #8 is still failing before
runner assignment, so the branch must remain Draft until an exact-head run actually executes the
required jobs.

## 2. Final release topology

```text
main-reviewed source
        |
        | push vX.Y.Z
        v
+-------------------------------+
| Source identity gate          |
| - full git history            |
| - tag reachable from main     |
| - exact version source        |
| - release note exists         |
| - CHANGELOG heading exists    |
+-------------------------------+
        |
        v
+-------------------------------+
| Exact-source deterministic    |
| quality gates                 |
| - make gates                  |
+-------------------------------+
        |
        v
+-------------------------------+
| Reproducible canonical build  |
| - SOURCE_DATE_EPOCH=git time  |
| - Hatch reproducible=true     |
| - wheel + sdist               |
| - verifier + twine check      |
| - rebuild again               |
| - byte-for-byte cmp           |
+-------------------------------+
        |
        v
+-------------------------------+
| Artifact validation fan-out   |
|                               |
| 15-cell wheel smoke           |
| 3 OS x Py 3.10-3.14           |
|                               |
| 3-OS atst[all] smoke          |
|                               |
| sdist -> wheel rebuild        |
+-------------------------------+
        |
        v
+-------------------------------+
| Canonical release envelope    |
| - wheel                       |
| - sdist                       |
| - SHA256SUMS.txt              |
| - RELEASE-METADATA.json       |
+-------------------------------+
        |
        v
+-------------------------------+
| Draft GitHub Release          |
| exact canonical assets only   |
+-------------------------------+
        |
        +-------------------------------+
        |                               |
        v                               v
PUBLIC_RELEASE != true        PUBLIC_RELEASE == true
private repository Release    PyPI state/hash/set check
        |                               |
        |                   absent -> OIDC publish
        |                   exact -> safe no-op
        |                   drift -> hard fail
        |                               |
        |                   PEP 740 attestations
        |                               |
        |                   post-publish readback
        |                               |
        |                   PUBLISH_DOCKER == true?
        |                        |
        |                        v
        |                   Docker publication
        |                   - pinned base digest
        |                   - provenance mode=max
        |                   - SBOM
        |
        +-------------------------------+
                        |
                        v
                Publish GitHub Release
```

## 3. Canonical version identity

The only writable package version is:

```text
atst/_version.py
```

All other surfaces derive from it:

```text
atst/_version.py
 -> Hatch dynamic version
 -> wheel metadata
 -> sdist metadata
 -> release tag verification
 -> installed atst.__version__
 -> GitHub Release identity
 -> PyPI version identity
 -> Docker release tags
```

`atst/__init__.py` only re-exports `__version__`.

There is no second manual version declaration.

## 4. Reproducible build contract

V3 makes reproducibility an executable gate rather than an assumption.

### 4.1 Build epoch

The release workflow sets:

```text
SOURCE_DATE_EPOCH = tagged commit timestamp
```

The timestamp is derived from the exact release commit:

```bash
git log -1 --format=%ct "$GITHUB_SHA"
```

### 4.2 Hatch reproducibility

`pyproject.toml` explicitly declares:

```toml
[tool.hatch.build]
reproducible = true
```

### 4.3 Double-build proof

The workflow builds canonical artifacts once into `dist/`, then rebuilds the same source into
`dist-repro/`.

It requires:

```text
dist/*.whl       == dist-repro/*.whl
dist/*.tar.gz    == dist-repro/*.tar.gz
```

using byte-for-byte `cmp`.

This is essential because retry safety depends on artifact SHA-256 identity.

## 5. Canonical artifact contract

A valid Python release contains exactly:

1. one `atst-<version>-py3-none-any.whl`;
2. one `atst-<version>.tar.gz`;
3. one `SHA256SUMS.txt`;
4. one `RELEASE-METADATA.json`.

No downstream surface may rebuild the wheel.

### RELEASE-METADATA.json

The metadata contains only stable release identity fields:

- schema version;
- repository;
- commit SHA;
- git ref;
- tag;
- package version;
- SOURCE_DATE_EPOCH;
- workflow name.

It deliberately excludes:

- run ID;
- run attempt;
- timestamps generated at workflow runtime.

Those values would change across a safe retry and would break idempotent verification.

## 6. Source eligibility gate

A tag is not sufficient by itself.

The release commit must satisfy:

```bash
git merge-base --is-ancestor "$GITHUB_SHA" origin/main
```

This prevents a maintainer from publishing an unmerged side branch under an official version tag.

The exact tagged source then reruns:

```text
make gates
```

before the canonical build begins.

## 7. Documentation identity gate

The canonical version must have:

```text
docs/releases/v<version>.md
```

and `CHANGELOG.md` must contain:

```text
## [<version>]
```

A package cannot be officially released without versioned release documentation.

The source distribution includes:

```text
docs/releases/*.md
```

instead of a hard-coded historical release note.

## 8. Compatibility matrix

### Core wheel

The exact canonical wheel is installed on:

| OS | Python |
|---|---|
| Ubuntu | 3.10, 3.11, 3.12, 3.13, 3.14 |
| macOS | 3.10, 3.11, 3.12, 3.13, 3.14 |
| Windows | 3.10, 3.11, 3.12, 3.13, 3.14 |

Total: 15 cells.

### Optional dependency closure

`atst[all]` is installed from the exact wheel on:

- Ubuntu / Python 3.14;
- macOS / Python 3.14;
- Windows / Python 3.14.

The workflow imports the declared optional dependency families and executes `pip check`.

### sdist

The exact canonical sdist must successfully produce a wheel that installs and exposes `py.typed`.

## 9. PyPI publication state machine

### 9.1 Pre-publication lookup

`scripts/check_pypi_release.py` queries the exact version from PyPI.

If absent:

```text
publish permitted
```

If present, the remote artifact set must be exactly equal to the local canonical Python
distribution set.

The comparison checks:

- missing remote files;
- extra remote files;
- SHA-256 mismatches.

Any difference fails closed.

### 9.2 Trusted Publishing

The workflow grants `id-token: write` only to the PyPI publication job.

No long-lived PyPI token is required.

### 9.3 Attestations

The official PyPA publication action is configured explicitly with:

```yaml
attestations: true
```

This produces PyPI/Sigstore publication attestations when using Trusted Publishing.

### 9.4 Post-publication readback

Action success alone is not final evidence.

After publication the workflow rereads PyPI and requires:

```text
remote artifact set == local canonical artifact set
and
remote SHA256 == local SHA256
```

The readback uses a bounded five-attempt loop with increasing short sleeps.

There is no unbounded retry.

## 10. GitHub Release state machine

### No existing Release

```text
create Draft
-> upload canonical release envelope
```

### Existing Draft

```text
resume
-> replace canonical assets with exact build
```

### Existing Published Release

The workflow requires exactly four explicit Release assets and downloads them.

It verifies:

- one wheel;
- one sdist;
- one checksum manifest;
- one release metadata file;
- checksum manifest validity;
- checksum manifest equality;
- release metadata equality.

If all match:

```text
verified no-op
```

Otherwise:

```text
hard fail
```

This prevents accidental or malicious mutation under an immutable release tag.

## 11. Docker publication contract

Docker is not the canonical Python release.

It is an optional projection controlled by:

```text
PUBLISH_DOCKER=true
```

and only runs after a successful public Python publication.

### 11.1 Exact wheel reuse

`Dockerfile.release` consumes only the already verified wheel from `release-dist/`.

It does not rebuild Python source.

### 11.2 Immutable base

The production release Dockerfile pins the Python base by digest:

```text
python:3.11-slim@sha256:<index-digest>
```

The human-readable tag remains only as context; the digest is authoritative.

### 11.3 Provenance and SBOM

The Docker publication requires:

```yaml
provenance: mode=max
sbom: true
```

so published images carry build provenance and a software bill of materials.

### 11.4 Stable channel rules

Only a plain final PEP 440 version can update stable Docker channels.

The following are excluded from `latest` and stable major/minor aliases:

- prerelease;
- dev release;
- post release;
- local version.

## 12. Publication variables

### PUBLIC_RELEASE

Unset or false:

- build;
- validate;
- create repository-visible GitHub Release;
- do not publish public PyPI;
- do not publish Docker.

True:

- enable public PyPI Trusted Publishing.

### PUBLISH_DOCKER

Unset or false:

- Docker job is explicitly skipped;
- Python release may complete normally.

True:

- publish Docker after PyPI succeeds.

## 13. Toolchain pins

Release-sensitive tools are direct, explicit dependencies:

- Hatchling;
- build;
- Twine;
- packaging.

The workflow must not depend on `packaging` merely being pulled transitively by another tool.

Release GitHub Actions are pinned to immutable commit SHAs.

## 14. Failure matrix

| Failure | Result | Recovery |
|---|---|---|
| tag != source version | stop | correct version/tag |
| tag commit not in main | stop | merge source first |
| release note missing | stop | add release note |
| changelog heading missing | stop | update changelog |
| deterministic gate fails | stop | fix source |
| first/second build differs | stop | remove nondeterminism |
| wheel verification fails | stop | fix packaging |
| one compatibility cell fails | stop | fix compatibility |
| extras fail | stop | fix optional deps |
| sdist rebuild fails | stop | fix sdist |
| GitHub Release absent | draft created | continue |
| existing Draft | resume | continue |
| published Release exact | verified no-op | none |
| published Release has extra asset | hard fail | investigate |
| PyPI absent | publish | continue |
| PyPI exact | safe no-op | continue |
| PyPI extra/missing/hash drift | hard fail | investigate |
| PyPI post-readback not visible | bounded retries then fail | rerun after service recovery |
| Docker disabled | skip | release continues |
| Docker enabled but fails | Release remains Draft | fix Docker and rerun |

## 15. Dead-loop analysis

The release workflow contains no recursive workflow dispatch and no unbounded poll loop.

All external operations are bounded by:

- job timeouts;
- finite matrix size;
- finite PyPI readback attempts;
- explicit reruns only.

No failure path automatically creates another release run.

Therefore there is no release dead loop in the designed state machine.

## 16. Orphan-logic analysis

### Canonical version

Single writer: `atst/_version.py`.

### Canonical Python artifacts

Single builder: `build-dist`.

### PyPI

Consumes the canonical artifact only.

### GitHub Release

Consumes the canonical release envelope only.

### Docker

Consumes the canonical wheel only.

### Documentation

Versioned release note and CHANGELOG entry are mandatory source gates.

### Tests

Release architecture is guarded by compatibility/contract tests, including:

- build package contracts;
- distribution identity;
- CI workflow contracts;
- Docker distribution contracts;
- PyPI retry contracts.

There is no alternative local publish shortcut: `make publish` intentionally fails.

## 17. Frontend/backend connectivity interpretation

This subsystem is release infrastructure, not a UI product, so "frontend/backend connected" maps to
publication surface connectivity:

```text
source
-> CI gates
-> build backend
-> artifact verifier
-> GitHub Actions artifact store
-> GitHub Release
-> PyPI
-> optional Docker registry
```

All declared release surfaces consume the same canonical artifact chain.

There is no independent frontend build path or second backend publisher.

## 18. Repository/platform controls outside source code

The repository source can enforce workflow logic, but some industrial controls live in GitHub/PyPI
administration.

Recommended controls:

- protected `main`;
- protected release tags;
- restricted workflow/variable/environment administration;
- protected `pypi` environment;
- PyPI Trusted Publisher bound to this repository/workflow/environment;
- least-privilege Docker secrets;
- periodic review of pinned Action SHAs and Docker base digest.

Current repository ruleset inspection returned a GitHub plan-level limitation for this private
repository, so source code must not pretend those platform controls were verified.

## 19. Current CI limitation

PR #8 remains Draft.

Recent CI attempts have repeatedly ended before runner assignment:

```text
runner_id = 0
steps = []
```

This means the configured gates have not executed.

The architecture may be considered code-complete only after static review, but release readiness
requires a real exact-head execution.

Acceptance requires at least one exact-head run where:

1. a runner is assigned;
2. checkout executes;
3. dependency installation executes;
4. Ruff/mypy/tests execute;
5. Python 3.14 executes;
6. release contract tests execute;
7. all required jobs reach terminal success.

## 20. Three incremental review rounds in V3

### Round 1 - reproducibility and retry correctness

Found:

- PEP 440 parser depended on transitive installation;
- retry hashes assumed reproducible artifacts without proving them;
- PyPI retry accepted a remote superset of artifacts.

Fixed:

- direct `packaging` pin;
- explicit Hatch reproducibility;
- commit-derived SOURCE_DATE_EPOCH;
- double-build byte comparison;
- exact remote artifact set equality.

### Round 2 - supply-chain provenance

Found:

- production Docker base used a movable tag;
- PyPI attestation behavior depended on action default;
- Docker publication did not explicitly request provenance/SBOM.

Fixed:

- immutable Docker base digest;
- explicit PyPI attestations;
- Docker provenance `mode=max`;
- Docker SBOM.

### Round 3 - auditability and final-state verification

Found:

- no machine-readable release identity asset;
- published GitHub Release retry did not reject extra explicit assets;
- PyPI action success was not followed by remote readback;
- versioned release documentation was advisory rather than enforced;
- first metadata design included run-specific fields and would break idempotency;
- one old workflow contract still asserted a superseded step name.

Fixed:

- stable `RELEASE-METADATA.json`;
- exact explicit GitHub Release asset count;
- PyPI post-publication bounded readback;
- release-note and CHANGELOG identity gates;
- removed volatile run fields from canonical metadata;
- realigned workflow contract tests.

## 21. Final acceptance checklist

Before moving PR #8 out of Draft:

- [ ] exact-head CI receives real runners;
- [ ] lint executes and passes;
- [ ] mypy executes and passes;
- [ ] offline tests execute and pass;
- [ ] Python 3.14 executes and passes;
- [ ] compatibility contract tests execute and pass;
- [ ] reproducibility test executes and passes;
- [ ] no workflow references stale paths;
- [ ] no new orphan release logic;
- [ ] no release workflow recursion;
- [ ] no publication occurs from the PR itself.

Before an actual public release:

- [ ] merge through main;
- [ ] update canonical version;
- [ ] add `docs/releases/v<version>.md`;
- [ ] update CHANGELOG;
- [ ] verify `PUBLIC_RELEASE` policy;
- [ ] verify PyPI Trusted Publisher;
- [ ] verify `PUBLISH_DOCKER` policy;
- [ ] review pinned Docker base digest;
- [ ] push matching tag;
- [ ] inspect complete release run;
- [ ] verify PyPI attestation;
- [ ] verify GitHub Release four canonical assets;
- [ ] verify Docker provenance/SBOM when Docker publishing is enabled.

## 22. Final assessment

At the architecture and source-contract level, this release subsystem now satisfies the major
properties expected from an industrial Python release pipeline:

```text
single source identity
+ reproducible build
+ build once
+ verify everywhere
+ exact artifact reuse
+ immutable/attested publication
+ bounded recovery
+ idempotent retry
+ audit metadata
+ explicit optional projections
+ contract-locked architecture
```

The only unresolved release-readiness item is external CI runner execution evidence. Until GitHub
actually runs the gates on the latest head, PR #8 must remain Draft and unmerged.
