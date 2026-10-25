# Documentation

Current, maintained documentation:

| Document | Purpose |
|---|---|
| [launcher-settings.md](launcher-settings.md) | Persisted settings map, defaults, load/validation/save and migration policy. |
| [local-diagnostics.md](local-diagnostics.md) | What is logged, what is exported, bounds, privacy and validation. |
| [dlss-sr.md](dlss-sr.md), [upscaler.md](upscaler.md) | Temporal reconstruction, presets and frame generation. |
| [resolution.md](resolution.md), [parallel_gpu.md](parallel_gpu.md), [motion_vectors.md](motion_vectors.md) | Render size, speaker layout, GPU threading and motion-vector design notes. |
| [keyboard-prompts.md](keyboard-prompts.md) | In-game button prompts and their screenshot evidence. |
| [LINUX_BUILD.md](LINUX_BUILD.md), [MODS.md](MODS.md) | Building and running the Linux GTK/AppImage path. |
| [diagnostics-translation-gaps.json](diagnostics-translation-gaps.json) | Historical inventory used by the strict localization regression test. |

Unresolved-blocker evidence (keep together, do not summarize away):

| Document | Purpose |
|---|---|
| [gemini-audit-triage.md](gemini-audit-triage.md) | Audit results, including CRIT-05/CRIT-06. |
| [windows-memory-test-plan.md](windows-memory-test-plan.md) | Windows memory characterization plan and results. |
| [mission-7.14-windows-candidate.md](mission-7.14-windows-candidate.md), [mission-7.14-upstream.json](mission-7.14-upstream.json) | Upstream synchronization decisions and commit provenance. |
| `tests/test_windows_memory.c`, `.github/workflows/runtime.yml`, `experiments/windows-placeholder-leaf-prototype/` | Executable characterization, CI and non-production prototype evidence. |

CRIT-06 (unmapped sibling-view gap and concurrent `0xC0000005` under Windows memory pressure)
is **unresolved**. The placeholder-leaf prototype is not a production fix and is not validation
of that issue.

[original-readme/](original-readme/) and [screenshots/](screenshots/) are archived upstream
provenance and evidence, not current build instructions.
