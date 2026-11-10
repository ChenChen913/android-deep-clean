<h1 align="center">Android / HarmonyOS Deep Clean</h1>

<p align="center">
  <b>English</b> | <a href="./README.md">简体中文</a>
  <br><br>
  <a href="https://github.com/ChenChen913/android-deep-clean/actions/workflows/ci.yml"><img src="https://github.com/ChenChen913/android-deep-clean/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/Python-3.9%2B-blue" alt="Python 3.9+">
</p>

AI-powered deep-clean skill for Android and HarmonyOS: read-only scan, evidence review, quarantine-based cleanup and restore.

> Status: the read-only pipeline has been verified on a real device over USB + ADB and over the on-device local transport. Quarantine, verify and restore have been exercised on a real device with self-created test files. **No cleanup of real user data has been accepted yet, and HarmonyOS NEXT has 0 real-device validations.** This is not yet a production-grade cleanup tool; see the [real-device acceptance plan](docs/real-device-test-plan.md).

Real terminal excerpt (HONOR HLK-AL00, HarmonyOS 3.0 compat / Android 10, USB + ADB, serial redacted):

```text
$ python "$Skill/scripts/adc.py" devices --adb-path adb
<serial>  device  product:HLK-AL00 model:HLK_AL00

$ python "$Skill/scripts/adc.py" scan --state .adc-state --transport adb --serial <serial> --json --quiet
{"roots_scanned": ["/sdcard"], "stats": {"entries": 125855, "files": 117007, "bytes": 25093192713}, "truncated": false}

$ python "$Skill/scripts/adc.py" analyze --state .adc-state --transport adb --serial <serial> --json --quiet
{"summary": {"candidate_groups": 59, "candidate_files": 74885,
             "candidate_bytes": 5390767059, "review_required": 56}}
```

The cleanup pipeline enforces this order:

```mermaid
flowchart LR
  A[doctor read-only probe] --> B[scan read-only]
  B --> C[analyze candidates + evidence]
  C --> D{AI per-item review}
  D -->|trash| E[plan]
  D -->|keep / uncertain| F[keep]
  E --> G[select list + token]
  G --> H{explicit user approval}
  H -->|no| F
  H -->|yes| I[execute quarantine / optional PC backup]
  I --> J[verify]
  J --> K{next step}
  K -->|done| L[quarantine purge to free space]
  K -->|not sure| M[restore]
```

## Contents

- [Why this project](#why-this-project)
- [Quick start](#quick-start)
- [Usage](#usage)
- [Configuration](#configuration)
- [Project structure](#project-structure)
- [Development](#development)
- [FAQ](#faq)
- [Known limitations](#known-limitations)
- [Contributing](#contributing)
- [License](#license)

## Why this project

Typical cleaners decide by folder name, file age or size. They can sweep away chat media, game saves inside uninstalled-app residue, or offline installers, and they rarely explain why a file was selected.

This project splits cleanup into auditable steps:

- Scanning is read-only. Every candidate reports paths, file count, size, age, and whether the owning app is still installed.
- The AI must review each candidate and write a reason. Unreviewed CAUTION items cannot enter the selection list.
- The user's selection produces a confirmation token. Execution without a token is refused.
- By default files are moved into an on-device quarantine area and can be restored. Permanent deletion is a separate, explicit second step.
- Before each operation the executor re-checks size, mtime and inode. Changed files are skipped and reported.

The goal is not fast deletion. It is finding long-accumulated files that ordinary cleaners miss without deleting important data.

## Quick start

Requirements:

| Item | Requirement |
|---|---|
| Python | 3.9+ (runtime code uses the standard library only; no pip install needed) |
| PC path (Android / HarmonyOS compat) | Windows 10/11 or macOS/Linux, Android official Platform Tools with `adb` (Android Studio is not required) |
| PC path (HarmonyOS NEXT) | HarmonyOS Command Line Tools or SDK toolchains with `hdc` (the full DevEco Studio IDE is not required) |
| Phone path (optional) | Android phone with Termux, or Shizuku with rish |
| Tests (optional) | pytest |

Install into the Agent Skills directory (Windows):

```powershell
git clone https://github.com/ChenChen913/android-deep-clean.git
cd android-deep-clean
powershell -ExecutionPolicy Bypass -File .\android-deep-clean\scripts\install_skill.ps1
python "$env:USERPROFILE\.agents\skills\android-deep-clean\scripts\adc.py" --version
```

The last command should print `android-deep-clean 1.11.0`. The installer backs up an existing directory, copies the new one, verifies `SKILL.md`, `scripts/adc.py` and `platform.py`, and rolls back on failure.

macOS / Linux installation into a generic skills directory:

```bash
git clone https://github.com/ChenChen913/android-deep-clean.git
mkdir -p ~/.agents/skills
cp -R android-deep-clean ~/.agents/skills/android-deep-clean
python ~/.agents/skills/android-deep-clean/scripts/adc.py --version
```

You can also run it without installing: `python android-deep-clean/scripts/adc.py --help`.

Uninstall (Windows):

```powershell
Remove-Item -Recurse -Force "$env:USERPROFILE\.agents\skills\android-deep-clean"
Remove-Item -Recurse -Force .adc-state
```

Deleting the state directory also deletes the restore journal for quarantined files. Only do it after confirming the quarantine is empty.

## Usage

Choose a path first. All three paths share the same classification, risk levels, protected-path rules, confirmation token, executor and verifier. Only the transport differs.

| Path | Devices | Connection | Key flags |
|---|---|---|---|
| A PC over ADB | Android, HarmonyOS 1.x–4.x compat | USB cable + USB debugging | `--transport adb --serial <serial>` |
| B On-device | Android, HarmonyOS 1.x–4.x compat | Termux local shell or Shizuku rish | `--transport local` or `--transport rish` |
| C PC over HDC | HarmonyOS NEXT (5+) | USB / wireless debugging + `hdc` | `--transport hdc --serial <connect-key> --hdc-path hdc` |

HarmonyOS NEXT cannot use the on-device path: it has no Android runtime, no Termux, no Shizuku and no adb.

### PC: full USB + ADB walkthrough

Common flags must come after the subcommand, for example `doctor --state ...`, not `--state ... doctor`. With more than one connected device you must pass `--serial`.

**Step 1: prepare the phone**

1. Settings → About phone, tap the build number seven times to enable Developer options.
2. Developer options → enable USB debugging.
3. Connect the USB cable. Either "File transfer" or "Charging only" works.
4. Unlock the phone and accept the "Allow USB debugging?" prompt.

**Step 2: prepare the PC**

Install Android official Platform Tools and confirm `adb version` works. Android Studio is not required.

**Step 3: confirm the device**

```powershell
$Skill = "$env:USERPROFILE\.agents\skills\android-deep-clean"
$State = ".adc-state"
$Serial = "<serial>"

python "$Skill\scripts\adc.py" devices --adb-path adb
```

| State | Meaning and action |
|---|---|
| `device` | Authorized, continue |
| `unauthorized` | Not allowed on the phone yet; reconnect and unlock |
| `offline` | Reconnect, or toggle USB debugging off and on |
| `no permissions` | Check the cable, USB mode and driver |

If `adb` is not on PATH, replace the path: `--adb-path "$env:LOCALAPPDATA\Android\platform-tools\adb.exe"`.

**Step 4: read-only probe**

```powershell
python "$Skill\scripts\adc.py" doctor --state $State --transport adb --serial $Serial
```

Read the device model, OS version, total/free storage, privilege tier, and the list of unreadable paths. If the tier is `blocked`, stop and do not pretend to scan.

**Step 5: read-only scan**

```powershell
python "$Skill\scripts\adc.py" scan --state $State --transport adb --serial $Serial
```

The default root is `/sdcard`; with root it also adds `/data/data/*/cache`. Useful flags: `--roots /sdcard/Download`, `--max-depth 32`, `--max-files 500000`, `--no-internal`. The result is a SQLite snapshot; no phone file is modified.

**Step 6: analyze and build evidence**

```powershell
# Hash duplicate candidates on the device (default)
python "$Skill\scripts\adc.py" analyze --state $State --transport adb --serial $Serial

# Or pull only same-size duplicate candidates to the PC and hash locally
python "$Skill\scripts\adc.py" analyze --state $State --transport adb --serial $Serial --hash-mode pc
```

Outputs:

| File | Purpose |
|---|---|
| `report-before.md` | Human-readable pre-clean report |
| `report-before.html` | Single-file visual report, opens offline in any browser |
| `analysis-summary.json` | Compact summary for the AI |
| `analysis.db` | Full candidate and per-file manifest |
| `review-template.json` | Review template |

**Step 7: AI review**

The AI reads `analysis-summary.json` and inspects large, risky or weakly evidenced candidates:

```powershell
python "$Skill\scripts\adc.py" inspect --state $State --transport adb --serial $Serial --id <candidate-id> --list-paths
```

Write the decisions to `review.json`. The format is documented in [SKILL.md](android-deep-clean/SKILL.md) and the [review guide](android-deep-clean/references/review-guide.md). Unreviewed CAUTION items cannot be selected.

**Step 8: build the plan**

```powershell
python "$Skill\scripts\adc.py" plan --state $State --transport adb --serial $Serial --review "$State\analysis\review.json"
```

`plan` only produces a plan. Show the selectable scope, risks and keep suggestions to the user.

**Step 9: user selection and token**

```powershell
# Select by category (example)
python "$Skill\scripts\adc.py" select --state $State --transport adb --serial $Serial --categories app_cache,thumbnails

# Select every selectable SAFE item
python "$Skill\scripts\adc.py" select --state $State --transport adb --serial $Serial --all-safe

# Select exact IDs (safest)
python "$Skill\scripts\adc.py" select --state $State --transport adb --serial $Serial --ids app_cache-xxxxxxxxxx
```

`select` does not execute. It prints the list and the confirmation token. Show paths, count, size and risk to the user and get an explicit yes. If the user declines, stop.

**Step 10: execute (quarantine by default)**

```powershell
python "$Skill\scripts\adc.py" execute --state $State --transport adb --serial $Serial --token <TOKEN>

# Copy selected files to the PC before cleaning; a failed backup keeps the source file
python "$Skill\scripts\adc.py" execute --state $State --transport adb --serial $Serial --token <TOKEN> --backup-to D:\phone-backup
```

Permanent deletion needs a separate confirmation. The selection must be created in delete mode and contain SAFE items only:

```powershell
python "$Skill\scripts\adc.py" select --state $State --transport adb --serial $Serial --all-safe --mode delete
python "$Skill\scripts\adc.py" execute --state $State --transport adb --serial $Serial --token <TOKEN> --mode delete --allow-permanent-delete
```

Files that changed, failed permission checks or are symlinks are skipped and reported. Do not retry them as a forced delete.

**Step 11: verify**

```powershell
python "$Skill\scripts\adc.py" verify --state $State --transport adb --serial $Serial
python "$Skill\scripts\adc.py" export --state $State --out D:\phone-report
```

The report separates logical processed bytes, actually freed bytes, and quarantine occupancy. In quarantine mode actually freed is 0 until `purge`.

**Step 12: restore or purge**

```powershell
# Restore all or specific IDs
python "$Skill\scripts\adc.py" restore --state $State --transport adb --serial $Serial --all
python "$Skill\scripts\adc.py" restore --state $State --transport adb --serial $Serial --ids <candidate-id>

# Permanently free the quarantine (irreversible; confirm again)
python "$Skill\scripts\adc.py" quarantine purge --state $State --transport adb --serial $Serial --token <PURGE_TOKEN>
```

Before purging or restoring, the command checks that each quarantine file still matches the size recorded in the journal. If `execute` used `--backup-to`, it also compares the SHA-256 with the backup. Restore additionally checks that `quarantine_path` is inside this execution's quarantine area and re-applies the category path rules to the target, so a tampered journal cannot move files from outside the quarantine. A file replaced or modified while in quarantine is refused and left in place; the reason is written to the journal and the report. Devices without a hash tool are explicitly marked as "size-only"; a hash computation error is recorded as "integrity check incomplete" and the operation is refused.

### On-device: Termux (`--transport local`)

Use this when no PC is available. The phone must run Python 3.9+.

```bash
# Termux (install from F-Droid)
pkg install python git
termux-setup-storage

git clone https://github.com/ChenChen913/android-deep-clean.git
cd android-deep-clean

python android-deep-clean/scripts/adc.py doctor  --state .adc-state --transport local
python android-deep-clean/scripts/adc.py scan    --state .adc-state --transport local
python android-deep-clean/scripts/adc.py analyze --state .adc-state --transport local
```

Differences from the PC path:

- No `--serial` or `--adb-path`.
- The state directory and quarantine live on the phone.
- Only shared storage is readable; `/data/data` is not.
- Whether `Android/data` is readable depends on scoped storage. The report lists the paths it could not open.

The remaining steps (inspect, plan, select, execute, verify, restore) are identical; replace `--transport adb --serial ...` with `--transport local`.

### On-device: Shizuku (`--transport rish`)

Shizuku exposes an ADB-shell-equivalent shell through rish:

```bash
python android-deep-clean/scripts/adc.py doctor  --state .adc-state --transport rish --rish-path /path/to/rish
python android-deep-clean/scripts/adc.py scan    --state .adc-state --transport rish --rish-path /path/to/rish
python android-deep-clean/scripts/adc.py analyze --state .adc-state --transport rish --rish-path /path/to/rish
```

Capabilities match an ADB shell; `/data/data` remains unreadable. Start Shizuku on the phone and confirm its service is running before use.

### HarmonyOS NEXT: PC over HDC

HarmonyOS NEXT has no Android runtime, so it requires `hdc` from a PC. Confirm the tool and device first:

```powershell
hdc list targets -v

python "$Skill\scripts\adc.py" doctor  --state $State --transport hdc --serial <connect-key> --hdc-path hdc
python "$Skill\scripts\adc.py" scan    --state $State --transport hdc --serial <connect-key> --hdc-path hdc
python "$Skill\scripts\adc.py" analyze --state $State --transport hdc --serial <connect-key> --hdc-path hdc
```

Boundaries:

- Only the public directory `/storage/media/100/local/files` and the media library `/mnt/data/<uid>/media_fuse/Photo`.
- No APK, adb, Shizuku or Termux; app-private caches cannot be scanned per file.
- `bm clean -c` is reported as a capability only and is never executed automatically.
- Path availability varies by device and user; trust the doctor report. NEXT has not been validated on a real device yet.

HarmonyOS 1.x–4.x compat keeps the Android runtime and uses the ADB path above.

### Four working modes

| Mode | Steps | Modifies the phone |
|---|---|---|
| A Read-only check | doctor → scan → analyze, produce the pre-clean report | No |
| B Deep clean | doctor → scan → analyze → AI review → plan → user select → execute → verify | Yes |
| C Targeted clean | scan/analyze, then narrow `select` by category, app kind or ID | Yes |
| D Re-check | `recheck` rescans and compares with the previous round | No (writes a new snapshot) |

Common targeted selections:

| Goal | Flag |
|---|---|
| WeChat / QQ caches | `--categories chat_cache` |
| Uninstalled-app residue | `--categories residue` (review per package) |
| Old APKs | `--categories apk` |
| Duplicate photos / videos | `--categories duplicates` (confirm per group) |
| Large files unused for a long time | `--categories large_old,media_old` |
| Browser caches | `--app-kinds browser` or `--categories browser_cache` |
| Empty directories | `--categories empty_dirs` (rmdir only) |
| SAFE items only | `--all-safe` |

### Cross-round directory archive and scan coverage

Since v1.3.0, `scan` also updates a host-side directory archive at `.adc-state/history/archive.db`:

```powershell
python "$Skill\scripts\adc.py" scan --state $State --transport adb --serial $Serial
python "$Skill\scripts\adc.py" archive diff --state $State
python "$Skill\scripts\adc.py" archive stats --state $State
```

The first scan creates a baseline. Later scans report new, growing, shrinking, missing, resurrected, suspected-orphan and unreviewed directories. `archive mark` only writes a `keep/watch/neutral` note:

```powershell
python "$Skill\scripts\adc.py" archive mark --state $State `
  --path /sdcard/DCIM/Camera --decision keep --note "user photos"
```

Marks are analysis hints only. They never change risk levels or authorize deletion. Incomplete scans, permission gaps and system/user changes suppress "vanished" conclusions. Use `scan --no-archive` to skip the archive update.

`scan-summary.md` and `report-before.md` also show coverage status, logical size, hardlink-deduped size, and the gap between device-used space and scanned files. That gap includes app-internal data, system data, unscanned areas and filesystem overhead; it is not reclaimable space.

### MediaStore auxiliary index

Since v1.4.0, `scan` optionally queries Android MediaStore via read-only `content query` for `images`, `video`, `audio` and `downloads`:

```powershell
python "$Skill\scripts\adc.py" scan --state $State --transport adb --serial $Serial
python "$Skill\scripts\adc.py" scan --state $State --transport adb --serial $Serial --no-media
```

Host-side output:

- `scan/aux/sources.json`: collection status, projection, limits.
- `scan/aux/media-index.jsonl`: deduplicated media index rows.

`analyze` cross-checks the index against the filesystem snapshot and adds a "MediaStore cross-check" section to `report-before.md` plus `summary.media_index` in `analysis-summary.json`:

- `matched`: both sources agree.
- `index_only`: in the index but not in the file snapshot; split into in-scope and out-of-scope. **Never treated as proof that a file was deleted.**
- `scan_only`: in the file snapshot but not in the index; may be unindexed, hidden or app-private media. **Not an anomaly.**
- `size_mismatch`: the index may be stale; filesystem wins.
- `trashed` / `pending`: index state hints only.
- **Zero-row diagnosis**: each collection is `ok` / `empty` / `zero_unconfirmed` / `zero_mismatch` / `failed` / `skipped`. A successful query with 0 rows has three outcomes: no **cross-checkable** same-kind files scanned -> `empty` (not a failure); 1 to threshold-1 same-kind files -> `zero_unconfirmed` (cannot prove the library is empty, and not an anomaly); at least the threshold (default 10) -> `zero_mismatch`, the overall status becomes `partial` and the report says the library must not be treated as empty.
- Zero-row cross-checking mirrors what MediaStore actually indexes: media whose path contains a hidden component (`/.xxx`) is not counted, because MediaStore does not index those paths (verified on a real device: `content query` returns no result for images under `.cache`, `.thumbnails` and `.gs`). The excluded count is reported in `crosscheck.hidden_excluded` and in the limitations, never silently dropped.
- `sources.json` records the actual command, projection, exit code, stderr, row count, attempts and elapsed time for diagnosis.

The media index is evidence only. It never creates candidates, changes risk or selectability, and its size is not added to the file-scan total.

Verified on HONOR HLK-AL00 (HarmonyOS 3.0 compat / Android 10 / SDK 29): 4,965 index rows / 7.35 GB; 4,964 matched, 8 differed only by path case, 1 was out of scan scope, 0 in-scope missing, 99.98% match. The `downloads` collection is unavailable on this ROM and degrades to partial. No files were modified.

### App-level storage (partial)

Since v1.5.0, `scan` reads the base APK size of installed packages:

```powershell
python "$Skill\scripts\adc.py" scan --state $State --transport adb --serial $Serial
python "$Skill\scripts\adc.py" scan --state $State --transport adb --serial $Serial --no-app-storage
```

It runs `pm list packages -f`, a bounded number of `pm path <package>` calls (default up to 120 apps) to find base and split APKs, and batched `stat`; it writes `scan/aux/app-storage.json`. Base APK stats need no root. Internal `/data/data` is only read with a bounded read-only `du` when the transport has root/su.

`analyze` adds `summary.app_storage` and an "App-level storage (partial)" report section:

- Known: base APK, discovered split APKs, external `Android/data` / `Android/media` / `Android/obb`, external cache.
- Conditionally known: app-internal data/cache/code_cache via read-only `du` under root/su (default up to 60 apps, prioritising large external-data packages).
- Unknown/partial: internal data under a plain shell, apps beyond the split-query budget, and paths that are missing, permission-denied or fail `stat`.
- Statuses distinguish `ok` / `partial` / `permission_denied` / `missing` / `no_path` / `stat_failed`; unknown data is never written as 0 MB.
- The base APK is `base.apk` when present. Vendor system apps that use another name (e.g. Huawei `Settings.apk` / `*_signed_entry.apk`) fall back to the first non-`split_*` entry in `pm path` order. When the base is unreadable, `apk_base_bytes` stays unknown and is **never replaced by a readable split**; readable splits are summed into `apk_split_bytes` only.
- Internal `collected` means `du` completed cleanly. A permission error or an unexplained non-zero exit marks the app `partial` even when some data was read: the value is a lower bound and the report says so. Missing `cache`/`code_cache` directories are normal and do not downgrade the result.
- Unknown internal cache values are never written as 0: a missing or empty directory is a real `0 B`, while a permission/IO failure stays unknown, is shown as “未知”, and the cache total is treated as a lower bound.
- Lists top apps by external storage and uninstalled packages that still have external directories.
- App-related candidates gain package-level evidence only; risk and selectability do not change.

**Why it is partial**: a plain ADB shell lacks StorageStatsManager permissions for other apps' internal data. `dumpsys diskstats` writes a system cache file, so it is not used because scans must stay read-only. Root/su internal stats use a bounded read-only `du` sample, not a full scan; split APK queries also have an app budget. The known total is not the app's total footprint and must not be added to device-used space.

Historical real-device result (v1.5.0 / HONOR HLK-AL00 / Android 10): 293 installed apps, 8.98 GB base APKs; 49 apps with external data totaling 1.92 GB; one uninstalled residue directory (109 B); external media/obb were 0. v1.10.2 re-validated on the same device (no root): 293/293 apps resolved to 9.00 GB base APKs + 20.78 MB splits (310 APK files), 2.15 GB external data; internal data stays unreadable under a plain shell as expected.

### Space story and candidate explanations (v1.6.0)

`report-before.md` now opens with a "Space story" section that does the bookkeeping for the reader:

- Lists device used, file scan, APK (base + split), readable internal data, app external directories and media index, each with its own measurement.
- Marks whether each row overlaps another one.
- Gives a "known and non-overlapping total" (file scan + APK + readable internal data) and an "unexplained gap".
- States explicitly that app external directories and the media index are already inside the file scan and must not be added again; internal cache is a subset of internal data.

Every candidate also gets three fixed lines: what it is, why it was listed, and what deleting it changes. These are readability-only and do not alter risk, selectability or the file manifest; `analysis-summary.json` items carry the same `explain` field.

Real-device example (HONOR HLK-AL00):

> Device used 78.81 GB; shared-storage scan 11.08 GB (14.1% of used); after adding base APKs, 58.75 GB remains unexplained.

### Single-use confirmation tokens (v1.7.0)

After a successful `execute`, the token hash is recorded in `execution/consumed-tokens.json`. Reusing the same token is refused (exit code 3):

- the token stays invalid even after `restore` puts the files back;
- continuing requires a fresh `scan → analyze → plan → select` and a new user confirmation;
- all five existing gates remain: `plan_hash`, `selection_hash`, device identity, file-manifest digest, and the per-file pre-execution `stat` check.

### Visual HTML reports (v1.8.0+)

`analyze` also writes `analysis/report-before.html`, which opens directly in a browser with no network access, scripts, fonts or other external assets:

- **Five trust states** run through the whole report: `known` / `partial` / `unknown` / `unconfirmed` / `failed`, each with its own icon and colour, plus a fixed legend. Unreadable numbers say "unknown" and never look like zero.
- **The first screen states the conclusion**: "of 79.59 GB used, 74.4% is unexplained", together with the reminder that nothing has been deleted.
- **Space story**: a segmented proportional band (file scan / base APKs / striped unexplained gap), per-source methodology cards and a "do not add these together" section.
- **Scan coverage, media crosscheck, app storage, directory changes** use the same data as the Markdown report; media uses paired index-vs-scan bars, app storage uses a horizontal ranking.
- **Candidates**: category distribution, three "10-second focus" cards (largest / highest risk / needs review) and a size-descending collapsible list that opens only the focused rows.
- Protected items, limitations and the safety boundary are listed separately, and the footer repeats that nothing has been deleted.

After execution, `execution/report-cleanup.html` is written and refreshed by `verify`, `quarantine purge` and `restore`. It turns the whole run into one archive:

- Process timeline: pre-clean findings → user selection → execution → verification → release or restore.
- The selected items, per-item execution results, failed paths and reasons.
- Verification stats and anomalies, quarantine occupancy, purge/restore results and free-space records.
- Remaining issues, suggested commands and safety notes; unverified steps are never shown as passed.

Both reports are rendered on the host, never touch the device, HTML-escape every path and evidence string, and show at most 60 candidates per run (the rest stay in `analysis.db` and `analysis.json` with identical fields). The layout adapts to the screen: below 560px every table becomes a labelled card list that reads top-to-bottom instead of scrolling sideways; dark mode follows `prefers-color-scheme`. They contain the device serial and file paths; redact before sharing. They do not change candidates, risks or selectability, and they are not deletion authorization. The previous renderer is kept in `html_report_legacy.py`; copying it back over `html_report.py` restores the old layout.

### Command reference

| Command | Purpose | Modifies the phone |
|---|---|---|
| `devices` | List ADB devices and authorization state | No |
| `doctor` | Probe device, storage, permissions and available commands | No |
| `scan` | Read-only scan, writes a SQLite snapshot | No |
| `analyze` | Build candidates, evidence, risks and the pre-clean report | No |
| `inspect` | Show one candidate's full manifest or preview | No (`--pull` only reads) |
| `plan` | Merge AI review decisions into a plan | No |
| `select` | Build the selection list and confirmation token | No |
| `execute` | Execute with the token; quarantine by default | Yes |
| `verify` | Verify the execution result | No |
| `quarantine purge` | Permanently delete quarantined files and free space | Yes |
| `restore` | Restore files from quarantine | Yes |
| `recheck` | Rescan and compare with the previous round | No (writes a new snapshot) |
| `archive diff/stats/mark` | Inspect cross-round directory changes and write analysis notes | No (host state only) |
| `export` | Export candidate, file and execution CSVs | No |
| `history` | List historical scan snapshots and archive stats | No |

Exit codes:

| Code | Meaning |
|---|---|
| 0 | Success |
| 2 | Usage error |
| 3 | Refused by safety policy (token, risk, no user approval, not selectable) |
| 4 | Device / transport error |
| 5 | Input or state-file validation failure |
| 6 | Partial failure in execute / verify / purge / restore (reports are still written) |
| 1 | Unexpected error |
| 130 | Interrupted |

### Safety boundaries (mandatory for callers)

- Never bypass this CLI with direct `rm`, `mv`, `find -delete`, `pm clear` or similar commands on the phone.
- Never add DANGEROUS items to a selection. Never lower a risk level.
- Never run `execute`, `quarantine purge` or `restore` without explicit user approval.
- Never delete system directories, app databases or account data, OBB files of installed apps, chat history or chat media, original personal photos/videos, or files of unknown purpose.
- The directory archive, cross-round changes and `archive mark` are evidence only; they must not create candidates or authorize deletion.
- Never follow symlinks. Never empty an entire directory. Only touch explicit paths from the candidate manifest.
- The CLI re-checks size, mtime and inode before each operation; changed files are skipped and reported.
- With multiple devices, always pass `--serial`. Execution verifies that the connected device matches the plan.
- With `--backup-to`, a failed backup or checksum mismatch must keep the source file.
- Never apply Android rules (`/sdcard`, `pm`, `Android/data`) to HarmonyOS NEXT.
- Mark capabilities that have not been verified on a real device as "unverified".

## Configuration

| Item | Required | Default | Description |
|---|---|---|---|
| `ADC_STATE` environment variable | No | `.adc-state` | Default state directory, equivalent to `--state` |
| `--state` | No | `.adc-state` | Directory for doctor/scan/analyze/plan/select/execute artifacts |
| `--transport` | Yes (defaults to adb) | `adb` | `adb`, `hdc`, `rish`, `local`, `mock` |
| `--serial` | Required with multiple devices | empty | ADB serial; connect-key for hdc |
| `--adb-path` | No | `adb` | Path to the `adb` executable |
| `--hdc-path` | No | `hdc` | Path to `hdc` for the hdc transport |
| `--rish-path` | No | `rish` | Path to rish for Shizuku |
| `--config` | No | built-in defaults | JSON overrides for thresholds and safety policy |
| `--json` / `--quiet` | No | off | Machine-readable output / fewer progress messages |

`--config` example:

```json
{
  "thresholds": {
    "apk_old_days": 180,
    "temp_old_days": 30,
    "download_old_days": 30
  },
  "safety": {
    "generic_cache_risk": "caution"
  }
}
```

Key default thresholds (source: `scripts/adc_lib/config.py`):

| Key | Default | Meaning |
|---|---|---|
| `large_file_bytes` | 104857600 | Large-file threshold (100 MiB) |
| `old_file_days` | 365 | Age threshold for large files |
| `apk_old_days` | 180 | Old APK threshold |
| `archive_old_days` | 180 | Old archive threshold |
| `temp_old_days` | 30 | Temporary file threshold |
| `log_old_days` | 90 | Log threshold |
| `screenshot_old_days` | 180 | Old screenshot threshold |
| `media_old_days` | 730 | Old media threshold |
| `download_old_days` | 30 | Old download threshold |
| `safety.generic_cache_risk` | `caution` | Generic cache policy; set to `safe` to restore the old behavior |
| `archive.min_dir_bytes` | 10485760 | Minimum directory size tracked by the archive (10 MiB) |
| `archive.grow_bytes` | 52428800 | Minimum growth treated as significant (50 MiB) |
| `archive.grow_percent` | 20 | Minimum growth percentage treated as significant |
| `media_store.enabled` | `true` | Enable the MediaStore auxiliary index |
| `media_store.collections` | four collections | `images/video/audio/downloads` |
| `media_store.timeout_seconds` | 60 | Per-collection query timeout |
| `media_store.max_items` | 200000 | Index row cap; marks the result truncated |
| `media_store.zero_crosscheck_min_files` | 10 | Minimum same-kind filesystem files before a 0-row result is `zero_mismatch` |
| `app_storage.enabled` | `true` | Collect APK and internal storage data |
| `app_storage.timeout_seconds` | 30 | Per-batch `stat` / `pm path` / `du` timeout |
| `app_storage.max_packages` | 1000 | Maximum packages collected |
| `app_storage.top_apps` | 15 | Apps shown in the report |
| `app_storage.split_apk_max_packages` | 120 | Maximum apps queried with `pm path`; the rest use base APKs only |
| `app_storage.internal_max_packages` | 60 | Maximum apps sampled with `du` under root/su |

## Project structure

```text
.
├── android-deep-clean/          # The skill itself
│   ├── SKILL.md                 # Instructions read by the agent
│   ├── scripts/
│   │   ├── adc.py               # CLI entry, 15 subcommands
│   │   ├── install_skill.ps1    # Backup, copy, verify, roll back on failure
│   │   └── adc_lib/             # Transport, scan, archive, MediaStore, app storage, reconcile, analyze, plan, execute, reports
│   └── references/              # Safety policy, path rules, review guide, USB/ADB, HarmonyOS
├── tests/                       # pytest cases and mock fixtures (no phone needed)
├── docs/                        # Install, CLI contract, HarmonyOS support, acceptance, test report
├── tools/build_dist.ps1         # Packaging script
└── .github/workflows/ci.yml     # ubuntu-22.04 / windows-latest × Python 3.9 / 3.13
```

## Development

Run the full suite (no phone, no network):

```powershell
python -m pytest tests -q
```

Measured on 2026-11-10 / v1.11.0: 327 passed, 2 skipped (329 collected). The skipped cases need symlink creation, which this Windows environment cannot do; Linux CI runs them.

CI matrix: `ubuntu-22.04`, `windows-latest` × Python `3.9`, `3.13` — 4 jobs. See [.github/workflows/ci.yml](.github/workflows/ci.yml).

Without a phone, use the mock transport to exercise the whole pipeline:

```powershell
python android-deep-clean\scripts\adc.py scan --state .tmp\state --transport mock --mock-root .tmp\phone --mock-packages .tmp\packages.json --mock-caps .tmp\caps.json
```

Fixture formats are documented in the [CLI contract](docs/cli-contract.md).

## FAQ

**Q: `adb devices` shows `unauthorized`.**
A: Unlock the phone and accept the RSA prompt. If no prompt appears, reconnect the cable, or toggle USB debugging off and on.

**Q: `Android/data` is unreadable. Is the skill broken?**
A: No. Android 11+ scoped storage limits a plain ADB shell. The doctor report marks the tier as `limited` and lists unreadable paths. It never pretends the scan was complete.

**Q: Execution succeeded but free space did not change.**
A: The default mode quarantines files by moving them, so they still occupy space. Run `quarantine purge` after confirming the result to actually free space.

**Q: Why can't I select CAUTION items directly?**
A: CAUTION items require an AI review written into `review.json`, followed by `plan`. Unreviewed CAUTION items never enter a selection. This is intentional.

**Q: Can HarmonyOS NEXT use adb?**
A: No. NEXT has no Android runtime and requires `--transport hdc`. Only HarmonyOS 1.x–4.x compat keeps the adb path.

**Q: Can I delete `.adc-state`?**
A: Yes, but you lose the restore journal for the quarantine, so `restore` can no longer recover those files.

**Q: Two phones are connected at the same time.**
A: Every device command must pass `--serial`. Plans, selections and executions are bound to a device identity; running them on another device is refused.

## Known limitations

- Without root, `/data/data` is unreadable; on some ROMs `Android/data` is unreadable too.
- Generic app caches and browser caches default to CAUTION and need per-item review. Only thumbnails and clear temporary logs default to SAFE.
- Quarantine does not free space. Reports separate quarantined, unprocessed, actually freed and quarantine occupancy.
- Similar-but-not-identical images are not compared visually; only exact duplicates are detected, and the user still confirms each group.
- atime is unreliable on Android, so age is based on mtime. Anomalous mtimes (before 2000, or more than 24 hours in the future) do not drive candidate selection.
- No system-level cleanup (system cache partitions, OTA). `pm`/`find` behavior differs across ROMs.
- Quarantine and restore depend on the local state directory; losing it prevents automatic restore.
- The directory archive is path-based: a rename appears as "missing + new". Incomplete scans and environment changes suppress vanished conclusions.
- The archive tracks directories ≥10 MiB and app directories by default; app-version detection needs `pm list packages --show-versioncode`.
- The directory archive and coverage passed read-only validation on one real device (HLK-AL00 / Android 10): 34,841 entries, 28,174 files, 11.35 GiB, `coverage=good`; other OEMs still need validation.
- The MediaStore index depends on `content query` and on shell/OEM behavior. It passed read-only validation on one HarmonyOS 3.0 compat / Android 10 device; the `downloads` collection was unavailable there. Other OEMs and Android versions still need validation. Index delays, multi-user profiles, hidden directories and some SD volumes can cause differences. Zero-row results are cross-checked against the filesystem scan: at least the threshold of cross-checkable same-kind files becomes `zero_mismatch`, 1 to threshold-1 becomes `zero_unconfirmed` (cannot prove empty), and no cross-checkable same-kind files becomes `empty`. Hidden paths are excluded because MediaStore does not index them, and the excluded count is reported separately.
- App-level storage: base APKs need no root; split-APK discovery has an app budget; internal data/cache are only sampled with read-only `du` under root/su. Plain shells are explicitly marked unavailable. `dumpsys diskstats` is not used because it writes a system cache; no StorageStatsManager helper APK is included.
- Hidden/special caches are matched only by name/extension plus an allowed root, and every such candidate is CAUTION/review-required. Vendor top-level directories may contain user data and are not auto-classified as cache.
- Scan coverage follows `find` stderr: a locked subdirectory downgrades coverage to `partial` and is listed by path. `doctor` samples at most 6 `Android/data` app folders, so unsampled apps can still be under-counted; the report never claims completeness for them.
- Quarantine path checks use `realpath` to narrow the TOCTOU window, but adb shell cannot provide `openat/O_NOFOLLOW` atomic guarantees; an unresolvable real path is always refused.
- Permanent delete (purge) has only been exercised on self-created test files; real user data is not accepted yet.
- HarmonyOS NEXT handles public directories and the media library only; app-private caches cannot be scanned per file, it does not use Android MediaStore, and `bm clean` is not wired into the executor.
- The on-device Termux / Shizuku paths have not been accepted on a real device.

Real-device status:

| Item | Status |
|---|---|
| Read-only on a real device (local + ADB) | 1 device passed: HONOR HLK-AL00, HarmonyOS 3.0 compat / Android 10 |
| MediaStore read-only index | 1 device passed: HONOR HLK-AL00; images/video/audio available, `downloads` unavailable on this ROM; 4,963 rows, 158 scan-only paths (not indexed or hidden, not an anomaly) |
| App-level storage (partial) | 1 device passed: HONOR HLK-AL00; 293 apps, 9.00 GB base APKs, 20.78 MB splits (120 apps queried within budget), 2.15 GB external data; internal data/cache not read (no root) |
| Quarantine / verify / restore | 1 device passed, self-created test files only |
| Cleanup of real user data | 0 devices accepted |
| Permanent delete (purge) | 1 device passed: two self-created test files (30 B); wrong token refused |
| Shizuku / native Termux | Not verified on a real device |
| HarmonyOS NEXT (hdc) | 0 devices accepted |

See the [test report](docs/test-report.md) and the [real-device acceptance plan](docs/real-device-test-plan.md).

## Contributing

Issues and pull requests are welcome. When changing cleanup rules or safety logic, add regression tests and keep `python -m pytest tests -q` green. Do not weaken safety assertions to make tests pass.

Report problems via [Issues](https://github.com/ChenChen913/android-deep-clean/issues). Before contributing, read the [safety policy](android-deep-clean/references/safety-policy.md) and the [CLI contract](docs/cli-contract.md).

Maintainer: repository owner [@ChenChen913](https://github.com/ChenChen913).

## License

[MIT](LICENSE) © 2026 Android Deep Clean contributors
