# Local diagnostics — Phases 1 and 2

## Using it

On the launcher's **Log** page:

- **Export diagnostics** prepares a bounded, sanitized **snapshot**. Select each file in
  the preview to inspect it (visible horizontal and vertical scrollbars), then **Save ZIP**
  to a new filename or **Cancel**. Saving
  writes precisely the previewed snapshot, even if the running application logs more
  information afterwards. Nothing is uploaded. Existing files are never overwritten.
- **Open logs folder** opens `%APPDATA%/uncharted-launcher/diagnostics`.
- **Copy** sanitizes the displayed runtime text before copying it.
- **Report a problem** asks before opening this fork's GitHub issue form. It does not
  attach the ZIP, append diagnostics to a URL, or upload any data. GitHub issues are
  public: manually share only files you have inspected and consent to publish.

For support/scripting, the actual packaged app also supports:

```powershell
& '.\Uncharted.exe' --export-diagnostics 'C:\Reports\uncharted-diagnostics.zip'
```

The directory must already exist and the file must not exist. This explicitly requested
local CLI export does not show the GUI preview; inspect the resulting ZIP yourself
before sharing. The GUI path remains the recommended user-facing workflow.

## What is recorded

Python startup initializes local diagnostics **before importing trophies, localization
or Tk** in the primary GUI, `--play`, and export roles. Internal `--run`, `--script`
and updater subprocesses do not rotate the primary session. Runtime/preparation output
from `run.py` and the native runtime is captured through the existing inherited pipe.
This is not a new runtime launcher or a native memory/crash handler.

`launcher.log` records UTC timestamps, severity, component, session ID, settings-load
fallbacks, launcher readiness, dependency probes, preparation/spawn errors, stop requests,
process start/exit and Python main-thread, worker-thread and Tk callback exceptions.
Each runtime attempt gets a new run ID; its sink is initialized **before Popen**, so
preparation and spawn failures do not depend on successfully creating a child process.
Runtime lines are persisted with timestamp/session/run context. Process exit status is
also represented as an unsigned hexadecimal value: it is an exit status, **not proof**
of a particular exception or root cause.

`session.json` persists the current stage, run settings allowlist, start/entry/exit times,
PID and exit code, stop-request intent and expected-shutdown classification. One previous
runtime record is retained when starting again. `previous-session.json` preserves the
preceding launcher session. A missing clean-shutdown record is described as **unknown**,
not automatically called a crash. Launcher shutdown is distinct from runtime shutdown:
closing the launcher while the game is active records that capture ended, not a verified
clean game exit. Existing close/stop behavior is not changed.

Runtime logs rotate to `previous_run.log` before a new attempt. The traditional
`<user_dir>/last_run.log` is kept as a bounded sanitized compatibility mirror, with its
own previous-run history. The stable per-user directory is authoritative; exports never
crawl the game/saves folder to find logs.

## Bounds and failure handling

Each managed log is capped at **256 KiB**, with at most **two size-rotation backups**.
Current and previous runtime logs are retained separately; launcher history is appended
and size-rotated. Individual writes are truncated near 8 KiB. Output is read in bounded
chunks and the GUI display queue is capped at 4,096 items; under overload it drops older
**display** items while persistent capture continues. Exit notifications remain queued.
Log write errors are deduplicated and surfaced through GUI warnings (or stderr before
the GUI is ready). They do not abort launch/capture or turn a failed process into success.
If storage is unwritable, evidence cannot be promised: session state continues in memory
but may not survive process death. Corrupt/oversized prior session files do not prevent
startup. File writes are flushed; this is not power-loss durability or an out-of-process
crash reporter.

Exports include allowlisted bounded text only:

- `diagnostics.json`: app/Python version, packaged source/base/upstream SHA when available,
  build time/target when available, OS version, architecture, CPU, observed GPU model/VRAM
  or an explicit unavailable status. Graphics-driver version is **not collected** yet.
  Development trees without BUILD-METADATA.json explicitly report missing build provenance.
- `launcher.log` and its most recent size backup; current/previous runtime logs and their
  most recent size backups when available (not every retained local backup).
- `session.json`, optional `previous-session.json`, `settings-redacted.json`.
- `README.txt` and `manifest.json`: included names, sizes and SHA256; omitted/unavailable
  members are explicit. The manifest does not hash itself.

The total **uncompressed** report payload is capped at **2 MiB**; oversized JSON is omitted,
not truncated into invalid JSON. Large log histories are tailed/truncated. Missing logs
on a first run are normal. Report generation does not read arbitrary directory contents,
follow log-file symbolic links or include dumps, saves, decrypted games, keys, full config,
environment dumps, raw command lines, hardware serials, hostnames or persistent user IDs.

## Privacy and limits

Settings use a narrow field/value allowlist (game identifier, relevant display/input
options). Paths, player names, extra environment, custom bindings and artwork/Steam
configuration are excluded. Known private settings, absolute paths, URLs, credentials,
email and IPv4 patterns are redacted from log text. JSON is sanitized structurally before
serialization. **Arbitrary runtime text can still contain unrecognized sensitive data**:
redaction is best effort, can over-redact useful text, and is not a guarantee of anonymity.
Always inspect every member before sharing; redact further or do not share if uncertain.
No native dumps or remote telemetry are collected or uploaded. Existing optional update
checks are unrelated and unchanged; disable upstream auto-updates on this fork candidate.

The first version assumes a single launcher instance using a profile; simultaneous
launcher processes sharing the same profile/history are not qualified. Hardware probing
can be unavailable; the exporter does not start expensive driver/WMI/ETW tracing.
Python cannot capture failures before its runtime/module initialization (e.g. missing
bootloader/DLLs), native call stacks or hangs. Crashpad/WER/minidumps, matching-symbol
retention, ETW/frame-time tracing and backend/aggregate analytics are deferred, not claimed
implemented. Diagnostic correctness does not repair CRIT-06 or qualify production memory.

## Validation and remaining human checks

`tests/test_local_diagnostics.py` covers startup history, clean/unclean sessions, corrupt
metadata, bounded rotation, private settings/text, source provenance, manifests and
snapshot consistency, oversized reports, symlink omission, no overwrite/partial ZIP,
simulated permission/disk-full failures, actual launcher early-import failure, actual
product CLI export, GUI spawn failure and output/exit capture under write failure,
and mocked preview scrollbar bindings, read-only file selection and exact-snapshot Save.
The preview has visible horizontal and vertical scrollbars.
The newly added UI text has translations in all six supported non-English languages;
the remaining coverage gaps are now translated as well. The complete original inventory
is in `diagnostics-translation-gaps.json`; strict coverage and format-field tests remain enabled.

The Windows package smoke test retains clean ZIP/hash/PE/provenance and actual default
GUI launch/normal-close/runtime-probe checks. It additionally invokes the shipped app's
product export CLI, verifies manifest hashes/source SHA/size bounds/previous GUI clean
shutdown, and checks that private fixture settings and the profile path do not leak.
Its sanitized test ZIP is preserved with CI evidence. This is **automated native Windows**
validation, not human interaction with the preview dialog and not gameplay.

Human checks still required: open every preview member, Cancel without writing, save to
an unused name, verify error on an existing/unwritable destination, Copy/Open Logs, open
and cancel the issue-form confirmation, start/stop a real game, inspect unexpected exits,
confirm previous evidence after restart, and test non-ASCII/long paths and multiple Windows
ACL configurations. Review privacy again before sharing. No native crash was induced in
a game or production memory manager for this mission.
