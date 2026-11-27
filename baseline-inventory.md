# Baseline inventory — W0 current state (2026-11-27)

Purpose: a truthful, evidence-backed current-state baseline for the R8
directive. This records what exists, what is gated or not run, and which
corrections are included in this release. It is not an acceptance
certificate. No claim of provider qualification, profitability, independent
acceptance, live execution or power-loss proof is made.

## 1. Verified git state

Commands were run in `/Users/tourist/Desktop/quant`. Exact outputs:

```console
$ git rev-parse HEAD
0b353f84f877764066eeb3dc0f70ac4ef645bf50
$ git log -1 --format=%s
Merge pull request #55 from siebengestirn/docs/canonical-inventory
$ git rev-parse HEAD^1
999680c5e6efb4b7a3c0a1de0fd9b1698072638c
$ git rev-parse HEAD^2
b3d7434a2957906558ecfa584f2384535a74a729
$ git branch --show-current
main
$ git branch -r
  origin/HEAD -> origin/main
  origin/main
$ git rev-parse origin/main
0b353f84f877764066eeb3dc0f70ac4ef645bf50
$ gh pr list --state open
$ gh pr list --state all --limit 100 | wc -l
55
$ gh pr list --state all --limit 100 | grep -c MERGED
55
$ git worktree list
/Users/tourist/Desktop/quant 0b353f8 [main]
$ git stash list
$ python3 scripts/check_git_policy.py --allow-dirty
QUANT git policy: PASS
```

`gh pr list --state open` and `git stash list` produced no output. `gh pr list
--state all --limit 100` lists PRs #1 through #55, all with state `MERGED`,
including PRs #47–#55. There are zero open PRs. `main` is the only local branch
and `origin/main` is the only remote tracking branch; local `main` equals
`origin/main` at the head above. One worktree, zero stashes. HEAD is the merge
commit of PR #55 (`docs/canonical-inventory`).

## 2. Governance at this head

| Control | Current value | Evidence |
|---|---|---|
| Model | `SINGLE_DEVELOPER` | `git-policy.json`, `docs/master-acceptance-register.md` |
| Required GitHub approvals | 0 | `git-policy.json` `pr_review` |
| Self-review checklist | REQUIRED | `git-policy.json`, `docs/git-workflow.md` |
| Exact-head CI | REQUIRED | `git-policy.json`, `docs/git-workflow.md` |
| Required checks | 3 | `git-policy.json` `github_protection_audit` |
| Independent external acceptance | PENDING / NOT RECORDED | CURRENT STATE block in the register |

The three required checks are `core build and tests`, `ASan/UBSan` and
`Style contract (reference guides)`. CI evidence is an automated gate; it is
not a human independent review. The historical one-approval material in
`docs/release-status.md` and the two branch-protection JSON exports are
labelled superseded; they do not establish the live GitHub
configuration. `git-policy.json` declares policy, and only an authenticated
API export proves live configuration; no such current export was produced in
this baseline. The single-maintainer exception is closed by scope and applies
only to PRs #45 and #46.

## 3. Canonical documentation inventory

`docs/README.md` is the canonical index. The bounded set is `docs/README.md`
plus ten tracked `docs/*.md` files; the count is 11 Markdown files total.
Current inventory:

| Document | Authority (from `docs/README.md`) |
|---|---|
| `docs/master-acceptance-register.md` | Requirements, workstreams, dependencies, exit evidence, release blockers |
| `docs/release-status.md` | Integrated state, verification results and operating limits |
| `docs/dataset-contract.md` | Snapshot manifest, SHA-256, CSV schema versions, timestamp policy |
| `docs/financial-integrity.md` | Integer money, postings, costs and replay invariants |
| `docs/risk-authorization-model.md` | In-process authorization capability and boundary |
| `docs/execution-compliance.md` | Zerodha lock, data-only mode, environment controls |
| `docs/git-workflow.md` | Branch model, merge policy, CI evidence, self-review rule |
| `docs/git-state.md` | Repository inventory and branch hygiene record |
| `docs/correctness-baseline.md` | Initial correctness requirements and findings |
| `docs/correctness-fixes.md` | Remediation history for the baseline findings |

Inventory policy: new material belongs inside one of those documents; extra
Markdown files are not admitted to `docs/`. Historical snapshots remain in Git
history and release bundles. `scripts/style_contract.py` enforces the policy
through the `docs-inventory` check: `docs/README.md` is required and at most
ten tracked `docs/*.md` files other than the index are allowed. The two W0
deliverables are new files at repository root, so they do not
alter the `docs/` inventory count.

## 4. Build and test evidence

### 4.1 Local verification (observed)

```console
$ ctest --test-dir build --output-on-failure
100% tests passed out of 3
$ ./build/quant_tests
checks=14063 failures=0
$ ./build-asan-ci/quant_tests
checks=14063 failures=0
$ ctest --test-dir build-asan-ci --output-on-failure
100% tests passed out of 3
$ python3 -m pytest tests/test_research.py \
    tests/test_dataset_contract.py \
    tests/test_dataset_adversarial.py
48 passed in 4.82s
$ python3 scripts/style_contract.py
reference-index: PASS
documentation: PASS
docs-inventory: PASS
json: PASS
tool-configs: PASS
shell: PASS
q-guide: PASS
```

The 48 pytest tests are 35 in `tests/test_research.py`, 7 in
`tests/test_dataset_contract.py` and 6 in `tests/test_dataset_adversarial.py`.
The `checks=14063 failures=0` result is from the portable `build` and from
`build-asan-ci`; the latter is the local sanitizer build configuration. All
results above are local observations, not hosted CI results and not
independent acceptance.

### 4.2 Native q limitation — GATED / NOT RUN

`build/CMakeCache.txt` records `QUANT_WITH_KDBX:BOOL=OFF`, and
`./build/quant doctor` reports
`c_api=not compiled runtime=available license=present pykx=absent`. The q
runtime exists at `$HOME/.kx/bin/q`, but the official `libe.so`/`k.h` pairing
is not available on this Mac, so the KDB-X C API was not compiled in. A full
`python3 -m pytest tests/ -q` run therefore produced two failures in
`tests/test_kdbx.py` with `KDB-X C API unavailable in this build`; those are
not treated as passes and are excluded from the 48-test count above. Native
q persistence and recovery tests cannot run locally.

The hosted `native-q` job in `.github/workflows/ci.yml` is guarded by
`if: vars.QUANT_NATIVE_Q_ENABLED == 'true'`, runs on `self-hosted` and is
explicitly not a required status check. Its status is GATED, NOT RUN. It is
not recorded as passed anywhere in this baseline.

### 4.3 Hosted evidence

Hosted exact-head CI is a required control. Prior hosted run identifiers and
per-PR dispositions are recorded in `docs/release-status.md` (including the
PR #34 style failure repaired by PR #35). Hosted exact-head CI evidence for
the release commit is recorded in GitHub Actions; this baseline does not
reproduce that result. `QUANT_WITH_KDBX=OFF` is the configuration used for
hosted core, sanitizer and style jobs.

## 5. Outstanding issues

The following are outstanding at this head. No item below is claimed closed.
Status words are conservative: `PENDING` means evidence is not
recorded, `PARTIAL` means a model or unit scope exists but not the full
contract, `GATED` means a guarded tier is not run, and `NOT PROVEN` means the
required evidence does not exist.

### P0 — release and safety blockers

| Item | Status | Source |
|---|---|---|
| Independent external technical acceptance | PENDING / NOT RECORDED | register CURRENT STATE, W16 |
| W1 canonical instrument master and multi-venue identity | NOT IMPLEMENTED beyond NSE cash-equity model | register W1 |
| W2 provider qualification (10-session, real endpoint) | PENDING; V3 endpoint not established | register W2, provider scope note |
| W4 provider-to-journal-to-q path with real events | NOT PROVEN; synthetic coverage only | register W4 |
| W12 power-loss durability qualification | NOT PROVEN; process/crash evidence only | register W12, correctness fixes Gate status |
| q server-side IPC authentication | NOT IMPLEMENTED | correctness fixes, Remaining blockers |
| Date-partitioned HDB with transactional checkpoints | NOT IMPLEMENTED | correctness fixes, Remaining blockers |
| W17 live execution authorisation | DISABLED; written broker/compliance approval absent | register W17, `src/zerodha.cpp:171` |
| Final evidence bundle regeneration | PENDING | `docs/release-status.md`, ACTIVE POLICY |

### P1 — correctness and operational gaps

| Item | Status | Source |
|---|---|---|
| W3 FYERS Data Socket adapter | NOT IMPLEMENTED; profile probe only, token expired | register W3, provider scope note |
| W5 exchange calendar and PIT universe | PARTIAL | register W5 |
| W6 corporate actions | NOT IMPLEMENTED | register W6 |
| W7 research and statistical acceptance | INCONCLUSIVE / BLOCKED; holdout SEALED_NOT_EVALUATED | register W7 |
| W8 feature registry | PARTIAL | register W8 |
| W9 contract-note reconciliation | NOT IMPLEMENTED | register W9 |
| W10 operational paper OMS | NOT IMPLEMENTED; CLI lifecycle not operational | register W10 |
| W11 native q hosted check | GATED / NOT RUN | register W11, `.github/workflows/ci.yml` |
| W15 multi-asset conformance | NOT ESTABLISHED | register W15 |
| White Reality Check, Hansen SPA, true CSCV/PBO | NOT IMPLEMENTED | correctness fixes, register W7 |
| QG-003 daily risk state persistence across restart | NOT IMPLEMENTED | correctness fixes, QG addendum |
| QG-004 research/execution universe separation | PARTIAL; price bands and full status data absent | correctness fixes, QG addendum |
| QG-007 cross-language 2^53 and large-timestamp fixtures | NOT IMPLEMENTED | correctness fixes, QG addendum |
| QG-008 process-kill and power-loss tests | NOT IMPLEMENTED | correctness fixes, QG addendum |
| QG-012..016 real pipeline, q security, PIT universe, statistics, audit lifecycle | NOT IMPLEMENTED / BLOCKED | correctness fixes |
| Composite idempotency key auto-restore into `.quant.seen` | PARTIAL; persisted in snapshots, not auto-restored | correctness fixes, Remaining blockers |
| Bounded async queue and batched typed IPC publishing | NOT IMPLEMENTED | correctness fixes, `README.md` |
| PIT universe, corporate actions, historical surveillance data | NOT IMPLEMENTED | correctness fixes, Remaining blockers |

### P2 — scope limits and follow-ups

| Item | Status | Source |
|---|---|---|
| W13 intraday support | UNSUPPORTED | register W13 |
| W14 HFT feasibility | SEPARATE PROJECT / UNSUPPORTED | register W14 |
| Zerodha authenticated read-only connectivity | NOT ESTABLISHED; no Kite session token | `README.md`, `./build/quant doctor` |
| Community Edition deployment limits | PENDING verification at deployment | `README.md`, KDB-X amendment |
| Provider streaming / real feed certification | NOT IMPLEMENTED / NOT ACHIEVED | register, provider scope note |

### R7 review items — corrections included in this release

These items are code and documentation changes included in this release.
Local verification is recorded in section 4; hosted exact-head CI evidence is
recorded in GitHub Actions. They are not independently reviewed or accepted.

| Item | Change | Status |
|---|---|---|
| R7-1 fail-closed ISO timestamp parser | Strict grammar, exact digit/range checks before calendar lookup and `timegm`; leap seconds and over-precision rejected; explicit `-1` sentinel | IMPLEMENTED; `src/market_data.cpp:668`, `src/market_data.cpp:686`, `tests/tests.cpp:965` |
| R7-2 bounded open-interest conversion | C++ rejects values `>= 2^63` before double-to-integer conversion; Python applies the same bound | IMPLEMENTED; `src/market_data.cpp:1107`, `python/dataset_contract.py:36` |
| R7-3 independent receive wall clock | `Quote::receive_wall_clock_ms`, populated from the system clock, written to the journal and restored on replay; distinct from monotonic receive time | IMPLEMENTED; `include/quant.hpp:441`, `src/engine.cpp:1178`, `src/engine.cpp:1269` |
| R7-4 Python dataset int64 bound | `MAX_OPEN_INTEREST = 2**63` with `OverflowError` handled | IMPLEMENTED; `python/dataset_contract.py:36`, `python/dataset_contract.py:409` |
| R7-5 documentation reconciliation | Active policy separated from superseded material; provider scope and OI/timestamp contract documented | IMPLEMENTED; `docs/README.md`, `docs/release-status.md`, `docs/master-acceptance-register.md`, `docs/dataset-contract.md` |

## 6. R8 correction release contents

The R8 fail-closed correction round covers these files:

- Parser and numeric safety: `src/market_data.cpp`, `include/quant.hpp`,
  `src/engine.cpp`, `python/dataset_contract.py`.
- Regression coverage: `tests/tests.cpp`,
  `tests/test_dataset_contract.py`.
- Documentation and policy: `README.md`, `docs/README.md`,
  `docs/dataset-contract.md`, `docs/git-state.md`,
  `docs/git-workflow.md`, `docs/master-acceptance-register.md`,
  `docs/release-status.md`, `git-policy.json`.
- Baseline and traceability: `baseline-inventory.md`,
  `architecture-current.md`, `technical-debt-register.csv`,
  `security-findings.csv`, `requirements-traceability.csv`,
  `research-report-registry.csv`, `.gitignore`.

Generated build directories are excluded from Git by `.gitignore`.

## 7. Operating status carried forward

Research is INCONCLUSIVE/negative; the holdout is SEALED_NOT_EVALUATED.
Upstox access is read-only V2 REST with no actual V3 endpoint call; V3
seven-field candle handling is CSV fixture-schema normalisation only. FYERS is
a profile probe only. There is no streaming feed. The paper OMS is not
operational. Power-loss durability is NOT PROVEN. Intraday and HFT are
UNSUPPORTED, multi-asset conformance is NOT ESTABLISHED, live execution is
DISABLED and the decision is NO TRADE.
