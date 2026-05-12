# Windows Terminal Support

Date: 2026-05-11
Updated: 2026-05-12

## Goal

Support users who run Claude Code or Codex from a Windows terminal, while keeping the existing macOS path unchanged.

The intended product behavior is:

- macOS users keep using `bash setup.sh` and `bash start.sh`.
- Windows PowerShell users use `.\setup.ps1` and `.\start.ps1`.
- Windows CMD users use `setup.cmd` and `start.cmd`.
- WSL users stay inside WSL and use the bash scripts.

This code is now on `main`. The first Windows user has successfully installed and used Margin from Windows.

## Boundary

This release supports the current terminal environment only.

That means:

- PowerShell/CMD backend calls the `claude` or `codex` available in that same Windows terminal.
- WSL backend calls the `claude` or `codex` available inside WSL.
- We do not bridge Windows backend to WSL CLI, or WSL backend to Windows CLI.

The bridge is intentionally out of scope because it creates path, credential, process, port, and file-permission ambiguity.

Also out of scope:

- Windows login-item / auto-start.
- GUI installer.
- Advanced file execution.

## Main Branch Risk

Windows support should stay on the main branch.

Why the risk is acceptable:

- macOS keeps its bash entrypoints: `setup.sh` and `start.sh`.
- Windows uses separate PowerShell/CMD wrappers and a separate Python launcher path.
- Runtime provider lookup only adds Windows executable discovery; it does not remove macOS paths.
- Frontend offline hints are platform-specific, so Mac users keep seeing the existing bash command.
- A real Windows user has already completed installation successfully.

Remaining risks:

- No automated Windows CI yet.
- Windows PowerShell execution policy can still block local scripts; the documented fallback is `powershell -ExecutionPolicy Bypass -File .\setup.ps1`.
- WSL/native mixing remains unsupported by design.

## User-Facing Commands

Use `setup` for first-time configuration in all public instructions.

Windows flow:

```powershell
git clone https://github.com/getupyang/knowledge-base-extension.git
cd knowledge-base-extension
.\setup.ps1
.\start.ps1
```

PowerShell execution policy fallback:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

Expected ready state:

```text
✓ 知识库服务器：http://localhost:8765
✓ Agent API：http://localhost:8766
✓ Worker：PID ...
```

## Frontend Offline Hints

Offline hints are platform-specific:

- Windows browsers show `.\start.ps1`.
- Non-Windows browsers keep the existing macOS/bash hint.

This keeps the old Mac experience stable while making Windows failure recovery understandable.

## Data And Config

The default local data path remains:

- macOS / WSL: `~/.knowledge-base-extension`
- Windows: `$HOME\.knowledge-base-extension`

The shared config file remains `~/.kb_config` as resolved by Python `Path.home()`.

Private user data must stay outside git:

- comments DB
- local profile and project context files
- learned rules
- logs and backups

## Validation

Validated on macOS:

- Python syntax for the Windows launcher and backend LLM client.
- JavaScript syntax for content, popup, and notebook files.
- `scripts/kb-health` stays green.
- Windows launcher `start/status` can smoke-run without disrupting existing macOS services.

Not yet validated:

- Automated Windows regression.
- Windows CMD path by a second user.
- Windows Claude Code and Codex CLI detection across multiple install methods.

When a Windows user reports an issue, ask for:

```powershell
py -3 scripts\memai_windows.py status
Get-Content "$HOME\.knowledge-base-extension\.logs\agent_api.log" -Tail 80
Get-Content "$HOME\.knowledge-base-extension\.logs\server.log" -Tail 80
Get-Content "$HOME\.knowledge-base-extension\.logs\worker.log" -Tail 80
```
