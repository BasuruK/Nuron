---
name: ponytail-help
description: >
  Quick-reference card for all ponytail modes, skills, and commands.
  One-shot display, not a persistent mode. Trigger: /ponytail-help,
  "ponytail help", "what ponytail commands", "how do I use ponytail".
---

# Ponytail Help

Display this reference card when invoked. One-shot, do not change mode,
write flag files, or persist anything.

## Levels

| Level | Trigger | What changes |
|-------|---------|--------------|
| **Lite** | /ponytail lite | Build what is asked, name the lazier alternative in one line. |
| **Full** | /ponytail | Ladder enforced: YAGNI -> stdlib -> native -> one line -> minimum. Default. |
| **Ultra** | /ponytail ultra | YAGNI extremist. Deletion before addition. Challenge requirement scope before building. |

Level sticks until changed or session end.

## Skill Map (1:1)

| Skill | Trigger | Purpose |
|------|---------|---------|
| **ponytail** | /ponytail | Simplest solution mode that still works. |
| **ponytail-review** | /ponytail-review | Over-engineering review on diffs only. |
| **ponytail-audit** | /ponytail-audit | Repo-wide over-engineering audit, ranked cuts. |
| **ponytail-debt** | /ponytail-debt | Harvest all ponytail comments into a debt ledger. |
| **ponytail-help** | /ponytail-help | This quick reference card. |

## Host Mapping

| Host | Invocation style |
|------|-------------------|
| GitHub Copilot in VS Code | Ask naturally with the skill name, for example: "use ponytail-review on this diff" or "run ponytail-audit". |
| Codex-style hosts | @ponytail, @ponytail-review, @ponytail-audit, @ponytail-debt, @ponytail-help |
| Claude Code / OpenCode | Slash commands: /ponytail, /ponytail-review, /ponytail-audit, /ponytail-debt, /ponytail-help |
| BMAD workflow | Use these agents as focused utilities between planning/build/review steps; they are one-shot helpers, not workflow state machines. |

## Deactivate

Say "stop ponytail" or "normal mode".
Also supported: /ponytail off.
Resume with /ponytail.

## Configure Default Mode

Default mode is full, auto-active every session.
Resolution order: environment variable > config file > full.

### Linux or macOS

```bash
export PONYTAIL_DEFAULT_MODE=ultra
```

### Windows PowerShell (current session)

```powershell
$env:PONYTAIL_DEFAULT_MODE = "ultra"
```

### Windows PowerShell (persist for current user)

```powershell
[System.Environment]::SetEnvironmentVariable("PONYTAIL_DEFAULT_MODE", "ultra", "User")
```

### Windows Command Prompt (persist for current user)

```cmd
setx PONYTAIL_DEFAULT_MODE ultra
```

Config file locations:
- Linux or macOS: ~/.config/ponytail/config.json
- Windows: %APPDATA%/ponytail/config.json

Example config:

```json
{ "defaultMode": "lite" }
```

Set off to disable auto-activation at session start and enable only when requested.

## Update

If your host supports plugin marketplace updates, use its native update flow.
For Claude Code:
- Enable auto-update in plugin marketplace once
- Manual refresh: /plugin marketplace update ponytail then /reload-plugins

If /plugin is unavailable, update Claude Code and restart.
Other hosts follow host-specific extension/plugin update paths.

## More

Full docs and examples: https://github.com/DietrichGebert/ponytail
