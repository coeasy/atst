# ATST Release Readiness V4

> Source branch: `fix/release-readiness-v4`  
> Merged PR: #9  
> Merge commit: `f712a0aad54f246bace7a6289c998295dfa46d09`  
> Date: 2026-09-28  
> Scope: post-PR-#8 release-readiness audit, remediation, and post-merge verification.

## 1. Executive conclusion

PR #8 materially improved the release architecture, but merging it exposed additional regressions that
would have made a real runner fail even though GitHub Actions was already failing before runner
assignment. Those source-level fixes were completed in PR #9 and merged to `main` as
`f712a0aad54f246bace7a6289c998295dfa46d09`.

V4 performs three complete review/fix rounds:

1. deterministic CI / installed-wheel gate integrity;
2. core runtime / configured-provider / health-surface consistency;
3. release supply chain / live evidence / publication recovery / documentation truthfulness.

All source-level issues found in these rounds have been repaired on this branch.

The repository is **not yet eligible for final release certification** because GitHub Actions still
fails before runner assignment. A second explicit rerun of main CI reproduced the same
`runner_id=0 / steps=[]` behavior, and the latest PR #9 exact-head runs show the same condition.
Therefore no Ruff, mypy, pytest, package build, or release-contract step has actually executed on
GitHub.

A second platform-level blocker also remains: `main` is currently unprotected and this connector
does not provide administration writes for branch protection / required checks. Industrial release
approval requires that governance control to be configured in GitHub.

## 2. Round 1 — deterministic gate regression repair

### 2.1 Stale local gate contract after Python 3.14

PR #8 added Python 3.14 to `.github/workflows/ci.yml`, changing the deterministic test matrix from
six to seven cells:

```text
Ubuntu: 3.10 / 3.11 / 3.12 / 3.13 / 3.14 = 5
Windows: 3.11 / 3.12                     = 2
total test cells                          = 7
```

With 11 workflow job keys, expanded CI evidence is now 17 checks.

The old compatibility contract still asserted 6 / 16, which would deterministically fail once a
runner executed it.

Fixed:

- `ci_test_matrix_cells() == 7`
- `ci_check_cells() == 17`
- the documented denominator is now 11 / 11 / 17.

### 2.2 Stale Makefile install assertion

`make install` was intentionally changed to install the project-declared dev toolchain plus pinned
pre-commit:

```text
python -m pip install -e ".[all,dev]" "pre-commit==4.6.2"
```

The old contract still demanded an ad-hoc `build twine` suffix.

The contract now follows the real dependency SSOT instead of requiring retired duplication.

### 2.3 Release wheel smoke regression

The release workflow rewrite had removed installed-wheel assertions that pre-existing contracts
expected and that protect release-only import/binding behavior.

Restored wheel-artifact smoke coverage includes:

- sync/async TDX/F10 client implementation ownership;
- `bestip` and `quotes_concurrent` async/sync wiring;
- sync/async direct TCP connection ownership;
- sync/async connection-pool ownership;
- generation-safe `update_hosts`;
- background speed-test binding;
- canonical `pool_settings_from_config` seam and absence of retired `from_config`;
- `RankingStore` / `resolve_hosts` canonical module ownership;
- async circuit helpers and close path.

The artifact smoke remains source-checkout-free, so these assertions validate the installed wheel,
not the repository tree.

## 3. Round 2 — runtime semantics and operational health

### 3.1 Problem: status metadata ignored configured runtime default

The core routing model distinguishes:

- capability declared by a Provider;
- capability operationally available on a Provider;
- omitted-provider routing under the runtime's configured default.

The first V3 status API computed default Provider from the global registry. This could disagree with
a real Client configured with, for example, `default_provider="eastmoney"`.

That created an operational observability split:

```text
real planner decision != health/status metadata
```

### 3.2 Fix: status is bound to the Client instance

`Client.core_capability_statuses()` is now an instance method and derives the effective default
from:

```text
self.runtime.planner.default_provider
```

`AsyncClient` delegates to its wrapped Client.

HTTP and WebSocket health consume the same instance method.

There is no second global provider-default table.

### 3.3 Any-provider availability vs default-path availability

The status payload now deliberately exposes two distinct facts:

- `available`: at least one Provider can operationally serve the capability;
- `default_available`: this Client's omitted-provider path is executable.

This distinction matters for configured Provider trust boundaries.

Example:

```text
default_provider = eastmoney

minute:
  available = true
  default_available = true
  default_provider = eastmoney

trades:
  available = true
  operational_providers include tencent / baidu
  default_available = false
  default_provider = null

security_list:
  available = false
  default_available = false
```

ATST does not silently switch a configured Provider that does not declare the requested capability.

### 3.4 Health surfaces

HTTP `/v13/runtime/health` and WS `runtime.health` compute `core_unavailable` from
`default_available`, not from the existence of any alternate Provider.

Therefore health describes the actual default execution path of that runtime instance.

### 3.5 Deterministic tests

The operational-default tests now explicitly pin their baseline runtime to TDX so user config or
environment cannot mutate deterministic CI expectations.

The trust boundary is also locked:

```text
QueryPlanner(default_provider="eastmoney")
minute -> eastmoney
trades -> ValidationError (no silent Tencent switch)
```

## 4. Round 3 — release supply chain and recovery

### 4.1 Immutable Actions across every workflow

V3 pinned third-party Actions only in the release workflow.

V4 pins the same immutable commit SHAs in:

- `ci.yml`
- `live-smoke.yml`
- `host-audit.yml`
- `wheels.yml`

A repository contract now scans every `uses:` entry in every workflow and rejects any third-party
Action that is not referenced by a 40-character commit SHA.

This makes the evidence-producing CI chain subject to the same supply-chain discipline as the
publication chain.

### 4.2 Live evidence for minute/trades operational defaults

The core readiness audit repaired omitted-provider routing:

```text
minute -> Tencent
trades -> Tencent
```

but no scheduled live assertion proved those routes.

V4 adds `tests/live/test_web_core_defaults.py`.

During an applicable A-share trading session it executes the real public Client path with runtime
policy pinned to TDX and **no per-request provider**. It then requires:

- planner-selected Provider == Tencent;
- no fallback provenance;
- non-empty minute data;
- non-empty trade data.

Outside the trading session the assertion is explicitly inapplicable and skips by clock/calendar
condition. Network/provider failure during an applicable session is not converted into a skip.

A compatibility contract ensures this live probe cannot be silently removed.

### 4.3 Publication promotion recovery

A V3 recovery bug prevented this legitimate lifecycle:

```text
verified GitHub Release (PUBLIC_RELEASE=false)
      ->
later enable PUBLIC_RELEASE=true
      ->
publish exact same canonical wheel/sdist to PyPI
```

Because `already_published=true` suppressed the PyPI job, the same verified release could never be
promoted.

V4 decouples GitHub Release publication state from PyPI idempotent state.

Now:

- an already-published GitHub Release must still match the canonical release envelope;
- `PUBLIC_RELEASE=true` always enters the PyPI idempotency verifier;
- missing PyPI version -> trusted publish;
- exact PyPI version/hash set -> no-op success;
- missing/extra/hash drift -> hard failure;
- optional Docker publication follows successful PyPI state and `PUBLISH_DOCKER=true`.

Fresh releases still use Draft -> external publication -> Publish ordering. The promotion path only
extends recovery for an already verified published Release.

### 4.4 Release documentation truthfulness

Current external distribution state was rechecked:

- GitHub repository currently has no Release;
- no `v1.0.0` ref is currently present;
- PyPI `atst` currently returns 404.

Therefore documentation claiming "latest published stable v1.0.0" was false.

V4 changes the public documentation to:

```text
current Release Candidate: 1.0.0
current distribution: no GitHub Release and not on PyPI
v1.0.0: planned first stable atst release
```

The release-history compatibility test now protects this truthful state rather than forcing a
nonexistent published release to be documented.

## 5. Core execution topology after V4

```text
Client / AsyncClient
    |
CLI / HTTP / WS / MCP
    |
QuerySpec
    |
QueryPlanner
    |-- configured/default Provider trust boundary
    |-- operational availability only where allowed
    v
one Provider + one Channel
    |
UnifiedRuntime
    |
DirectProviderExecutor
    |
TDX / Web adapter / local_vipdoc
    |
QueryResult + Provenance
    |
serialization / output
```

No additional runtime/router/cache path was added.

## 6. Loop / orphan / lifecycle review

No new recursive workflow dispatch was introduced.

Publication retry loops remain bounded.

PyPI readback uses a fixed five-attempt loop.

Live smoke scheduling does not self-trigger.

Core routing has one provider-selection point at Query normalization/planning.

Status metadata is now derived from the same planner default.

Installed-wheel smoke consumes the built artifact without source checkout.

The new Web live probe is connected to the existing `-m network` scheduled workflow and protected
by a compatibility contract.

## 7. Current release identity

The package version remains:

```text
1.0.0
```

This is currently valid as a release candidate because the GitHub repository has no current
`v1.0.0` ref and PyPI has no `atst` project/version.

The release workflow still requires:

- source version == tag;
- `docs/releases/v<version>.md` exists;
- CHANGELOG contains the version heading;
- tagged commit is reachable from main;
- deterministic `make gates` passes on the exact tagged source.

No tag or public publication is created by PR #9.

## 8. External blockers to final release certification

### 8.1 GitHub Actions runner assignment

Main CI run `36387343065`:

- attempt 1: jobs fail before runner assignment;
- explicit failed-job rerun / attempt 2: same pattern.

PR #9 exact-head runs also fail with `steps=null`.

This means the repository does **not** currently possess GitHub execution evidence for:

- Ruff;
- Ruff formatter;
- mypy;
- Python 3.10-3.14 tests;
- Windows tests;
- golden gate;
- adversarial matrix;
- reachability;
- originality;
- benchmark;
- docs links;
- newly added V4 contracts.

This is the primary release blocker.

### 8.2 Main branch governance

The main branch is currently unprotected.

The available GitHub connector exposes read operations for branch protection/rulesets but no
administration write action.

Before industrial release approval, GitHub repository administration should require the
deterministic CI checks on main/PR merge and restrict direct release-critical changes.

This control cannot be substituted by Python source code.

### 8.3 External publication configuration

Before a public PyPI release:

- verify the `pypi` GitHub Environment;
- verify PyPI Trusted Publisher binding to this repository/workflow/environment;
- set `PUBLIC_RELEASE=true` only when public publication is intended;
- set `PUBLISH_DOCKER=true` only when Docker Hub credentials and publication are intended.

## 9. Release-condition checklist

Source-level V4:

- [x] deterministic gate contracts aligned with Python 3.14;
- [x] installed-wheel regression coverage restored;
- [x] configured Provider status == actual planner semantics;
- [x] health uses default-path availability;
- [x] deterministic tests are config-independent;
- [x] all workflow Actions SHA-pinned;
- [x] minute/trades scheduled live proof added;
- [x] PyPI promotion recovery path repaired;
- [x] documentation reflects actual publication state;
- [x] no new hidden fallback;
- [x] no new unbounded loop;
- [x] no orphan publication path found.

Required external evidence before release:

- [ ] latest exact-head CI receives real runners;
- [ ] all deterministic jobs execute and pass;
- [ ] release contract tests execute and pass;
- [ ] scheduled/intraday live smoke obtains real evidence;
- [ ] main branch required-check protection is configured;
- [ ] PyPI Trusted Publisher / GitHub Environment is verified if public release is enabled.

## 10. Final decision rule

ATST is now **merged/source-ready but not release-certified** until the external checklist above
is satisfied.

The correct transition is:

```text
PR #9 merged to main
  -> restore real GitHub runner execution
  -> real main CI execution
  -> configure protected-main required checks
  -> intraday live evidence
  -> tag v1.0.0
  -> release workflow exact-source gates
  -> Draft Release
  -> optional PyPI/Docker publication
  -> Published Release
```

Do not bypass the runner/required-check evidence by manually publishing artifacts.
