---
name: hermes-agent
description: Comprehensive documentation and operational guide for Nous Research's Hermes Agent and Nous Portal. Use when installing, configuring, operating, debugging, or extending Hermes Agent, connecting to Nous Portal (portal.nousresearch.com), configuring the Tool Gateway, developing custom skills (agentskills.io) and plugins, setting up multi-platform messaging gateways (Telegram, Discord, Slack), selecting terminal backends (Docker, Modal, Daytona, SSH, local), or running scheduled cron automations and subagents.
metadata:
  hermes:
    requires_toolsets: [terminal, file, web]
---

# Hermes Agent — Complete Reference & Operational Guide

> **Official Documentation Portal:** [hermes-agent.nousresearch.com](https://hermes-agent.nousresearch.com/docs)  
> **Nous Portal:** [portal.nousresearch.com](https://portal.nousresearch.com)  
> **GitHub Repository:** [github.com/NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)  
> **Standard:** Compatible with the [agentskills.io](https://agentskills.io) open standard.

---

## 1. Overview & Architecture

**Hermes Agent** is the autonomous, self-improving AI agent engineered by **Nous Research**. Designed to break free from single-session and laptop-bound limitations, Hermes operates on a $5 VPS, GPU clusters, or serverless cloud backends, interfacing with users through terminal TUIs, messaging apps, and automated schedules.

### Core Architectural Pillars
1. **Closed Learning Loop**:
   - **Autonomous Skill Generation**: Automatically codifies repeated or complex successful workflows into reusable skills.
   - **Skill Self-Improvement**: Dynamically refines and updates existing skills during execution when edge cases or corrections occur.
   - **Curated Persistent Memory**: Stores user preferences, project facts, and session knowledge across time. Periodic nudges prevent knowledge loss.
   - **FTS5 Session Search**: Full-text SQLite search with LLM-powered summarization for precise historical recall across sessions.
   - **Honcho Dialectic User Modeling**: Learns a deepening behavioral profile of the user across channels.
2. **Unified Messaging Gateway**:
   - A single gateway daemon routes messages between the agent and Telegram, Discord, Slack, WhatsApp, Signal, and the CLI.
   - Preserves conversation continuity across devices and platforms with native voice memo transcription.
3. **Execution Everywhere (7 Terminal Backends)**:
   - Run commands locally, inside Docker containers, across SSH, in Singularity, or inside serverless hibernating microVMs via **Daytona**, **Modal**, or **Vercel Sandbox**.
4. **Subagent Delegation & RPC Tool Calling**:
   - Concurrently spawns isolated subagents with clean context windows via `delegate_task`.
   - Supports zero-context-cost script execution by calling tools via RPC directly inside Python scripts.
5. **Nous Portal & Tool Gateway**:
   - One-shot OAuth authentication via `portal.nousresearch.com`.
   - Unified subscription unlocking 300+ frontier models and hosted tools (web search, Firecrawl scraping, Browserbase automation, image gen, audio) without juggling separate API keys.

---

## 2. Nous Portal Integration (`portal.nousresearch.com`)

The **Nous Portal** serves as the unified account, subscription, and tooling hub for Hermes Agent.

### 2.1 The One-Shot Setup
The recommended configuration path for any new Hermes installation is:
```bash
hermes setup --portal
```
This single command automates:
1. **OAuth Authentication**: Launches a browser session to `portal.nousresearch.com` to authenticate your account/subscription.
2. **Credential Storage**: Securely stores the refresh token in `~/.hermes/auth.json`.
3. **Provider Configuration**: Sets `model.provider: nous` in `~/.hermes/config.yaml`.
4. **Default Model Selection**: Configures a recommended agentic reasoning model (e.g. Anthropic Claude 3.5 Sonnet or Nous Hermes 3).
5. **Tool Gateway Activation**: Instantly enables hosted tools without per-service API keys.

### 2.2 Remote / Headless Authentication
When deploying Hermes on a headless VPS or remote server:
- **SSH Port Forwarding (Recommended)**:
  ```bash
  ssh -N -L 8642:127.0.0.1:8642 user@remote-host
  ```
  Run `hermes setup --portal` on the remote host, then click the URL in your local browser.
- **Device-Code Flow**: If port forwarding is unavailable (e.g., Cloud Shell or Codespaces), Hermes provides a device-code login link directly in the terminal.

### 2.3 The Tool Gateway
The Tool Gateway routes calls to third-party APIs through your Nous Portal subscription:
- **Web Search**: Real-time web index search.
- **Web Scraping & Crawling**: Powered by Firecrawl.
- **Browser Automation**: Powered by Browserbase / Browser Use for headless interaction and visual testing.
- **Image Generation**: Hosted diffusion / generative models.
- **Text-to-Speech (TTS) & Audio**: Hosted speech synthesis and transcription.

**Bring Your Own Keys (BYOK)**: The gateway never locks you in. You can override any tool by setting the corresponding API key in `~/.hermes/.env` (e.g., `FIRECRAWL_API_KEY=...` or `OPENAI_API_KEY=...`).

### 2.4 Portal CLI Commands
```bash
# View portal login status, subscription tier, and current provider routing
hermes portal info

# Alias for portal info
hermes portal status

# Inspect the active Tool Gateway catalog and per-tool route configuration
hermes portal tools
```

---

## 3. Installation & Quickstart

### 3.1 Install Commands

**Linux, macOS, WSL2, Termux:**
```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
```

**Windows (Native PowerShell):**
```powershell
iex (irm https://hermes-agent.nousresearch.com/install.ps1)
```
*Note for Windows*: Native Windows runs Hermes without WSL. The installer sets up Python 3.11, `uv`, Node.js, ripgrep, ffmpeg, and an isolated MinGit instance in `%LOCALAPPDATA%\hermes\git`.

### 3.2 Post-Install Shell Reload & First Run
```bash
source ~/.bashrc   # or source ~/.zshrc
hermes setup --portal
hermes             # Launches the interactive TUI
```

---

## 4. Configuration Reference (`~/.hermes/`)

All configuration lives in `~/.hermes/` (or `%LOCALAPPDATA%\hermes\` on native Windows).

```
~/.hermes/
├── config.yaml          # Core settings (models, providers, terminal, skills)
├── .env                 # API keys, private tokens, secrets
├── auth.json            # OAuth tokens (Nous Portal, GitHub, etc.)
├── SOUL.md              # Agent persona, identity, and global directives
├── memories/            # Persistent SQLite DB, FTS5 index, Honcho state
├── skills/              # Reusable skills (~/.hermes/skills/<name>/SKILL.md)
└── plugins/             # Installed plugins (~/.hermes/plugins/<name>/)
```

### 4.1 `config.yaml` Structure
```yaml
model:
  provider: nous                      # nous, openrouter, openai, anthropic, custom
  default: anthropic/claude-3.5-sonnet
  auxiliary:
    vision: anthropic/claude-3.5-sonnet
    compression: nousresearch/hermes-3-llama-3.1-8b

terminal:
  backend: local                      # local, docker, ssh, modal, daytona, singularity, vercel_sandbox
  timeout: 300
  docker:
    image: python:3.11-slim
    workdir: /workspace

skills:
  config:
    auto_learn: true                  # Enable autonomous skill generation
    strict_env: false

memory:
  provider: builtin                   # builtin, honcho, mem0, openviking
  auto_nudge: true

gateway:
  platforms:
    telegram:
      enabled: true
    discord:
      enabled: false
```

### 4.2 `.env` File
Used for sensitive environment variables and API overrides:
```bash
# Tool Gateway Overrides (optional)
OPENAI_API_KEY="sk-..."
ANTHROPIC_API_KEY="sk-ant-..."
FIRECRAWL_API_KEY="fc-..."

# Gateway Tokens
TELEGRAM_BOT_TOKEN="123456:ABC-DEF..."
DISCORD_BOT_TOKEN="..."
```

### 4.3 `SOUL.md` vs `AGENTS.md`
- **`SOUL.md`**: Defines **who the agent is** — communication style, tone, personality, core safety boundaries, and persona traits. *Keep it concise.*
- **`AGENTS.md`**: Defines **what the agent does in this repository** — local project rules, deterministic pipelines, forbidden files, and operational runbooks.
- *Best practice*: Do not bloat `SOUL.md` with project commands. Put repo instructions into `AGENTS.md` and procedural tasks into `SKILL.md`.

---

## 5. CLI Command Reference

| Command | Action |
|---|---|
| `hermes` | Launch interactive terminal UI (TUI) |
| `hermes chat "<prompt>"` | Run a one-off prompt non-interactively |
| `hermes run "<command>"` | Run a specific tool or command pipeline |
| `hermes setup` | Launch the interactive configuration wizard |
| `hermes setup --portal` | One-shot setup with Nous Portal OAuth and Tool Gateway |
| `hermes setup terminal` | Configure execution environments (Docker, Modal, Daytona, etc.) |
| `hermes model` | Switch active reasoning/auxiliary models and providers |
| `hermes portal info` | Inspect Nous Portal connection, subscription, and model routing |
| `hermes portal tools` | List active Tool Gateway tools and endpoints |
| `hermes gateway` | Run the messaging gateway daemon in foreground |
| `hermes gateway install` | Install the gateway as a systemd / system boot service |
| `hermes gateway status` | Check running messaging gateway status |
| `hermes cron list` | List scheduled cron jobs and next trigger times |
| `hermes cron status` | Inspect cron executor daemon status |
| `hermes skills list` | List available skills across project, user, and bundled scopes |
| `hermes config get <key>` | Read config value (e.g. `hermes config get skills.config --json`) |
| `hermes config set <key> <val>` | Update a `config.yaml` property |
| `hermes doctor` | Run environment diagnostics, dependency checks, and health tests |

---

## 6. The Skills System ([agentskills.io](https://agentskills.io))

Hermes Agent skills follow the open `agentskills.io` standard. Skills teach the agent multi-step procedures, runbooks, and domain workflows.

### 6.1 Skill Directory & File Structure
A skill is stored in a directory containing `SKILL.md`:
```
<skill-name>/
├── SKILL.md             # Required: frontmatter + markdown instructions
├── scripts/             # Optional: helper scripts called by the skill
├── references/          # Optional: detailed markdown references or specs
└── examples/            # Optional: input/output examples
```

### 6.2 Precedence Order
When loading skills, Hermes resolves conflicts using this hierarchy (highest to lowest priority):
1. **Project Directory**: `./.agents/skills/<name>/SKILL.md` or `./hermes/skills/<name>/SKILL.md`
2. **User Directory**: `~/.hermes/skills/<name>/SKILL.md`
3. **Bundled / Plugin Skills**: Shipped with Hermes or bundled inside plugins.

### 6.3 `SKILL.md` Specification
```yaml
---
name: my-workflow
description: Clear, concise description of when to activate this skill and what it accomplishes.
metadata:
  hermes:
    requires_toolsets: [terminal, file, web]
    requires_env: [MY_API_KEY]
---

# Workflow Title

## When to use
Trigger phrases, conditions, and scenarios.

## Mental Model / Rules
Core invariants, forbidden paths, and deterministic requirements.

## Procedure
Step-by-step instructions and command invocations.
```

### 6.4 The Learning Loop & `/learn`
- **Autonomous Learning**: After completing complex tasks, Hermes summarizes its procedure and drafts a skill in `~/.hermes/skills/`.
- **Manual Trigger**: Use `/learn` in chat to instruct Hermes to codify the current session's solution into a permanent skill.
- **Skill Self-Improvement**: When Hermes encounters an error or receives a correction while executing an existing skill, it updates the `SKILL.md` file in place.
- **Session Hygiene**: When working on large tasks across days, use the `/new` command when switching tasks. This commits session history to SQLite/FTS5 so the memory and learning loop can search and recall it effectively.

---

## 7. Plugin System

Plugins extend Hermes with custom tools, hooks, messaging platforms, memory providers, and execution environments.

### 7.1 Structure of `~/.hermes/plugins/<plugin-name>/`
```
~/.hermes/plugins/my-plugin/
├── plugin.yaml          # Plugin manifest
├── __init__.py          # Entry point and tool/hook registration
├── tools.py             # Tool handler implementations
└── schemas.py           # Parameter schemas (Pydantic / dataclass)
```

### 7.2 `plugin.yaml` Schema
```yaml
name: my-plugin
label: "My Custom Plugin"
version: "1.0.0"
kind: toolset                     # toolset, platform, memory, terminal
description: "Adds domain tools to Hermes."
requires_env:
  - MY_SERVICE_KEY
optional_env:
  - MY_SERVICE_URL
cron_deliver_env_var: "MY_PLATFORM_CHAT_ID"  # For platform plugins
```

### 7.3 Plugin Features
- **Namespaced Skills**: Plugins can bundle their own skills using the `plugin:skill` naming convention to avoid namespace collisions.
- **Lazy Dependency Loading**: Dependencies declared in plugins can be lazily loaded to preserve startup speed and isolation.

---

## 8. Toolsets & Subagents

### 8.1 Toolsets
Hermes organizes capabilities into toolsets:
- `terminal`: Command execution, process monitoring, background tasks.
- `file`: Reading, writing, editing, and searching local files.
- `web`: Web search, URL retrieval, and page inspection.
- `browser`: Headless browser control via Browserbase / Playwright.
- `debugging`: Composite bundle combining `terminal`, `file`, and `web`.
- `hermes-cli`: Internal management of config, skills, and memory.

Enable specific toolsets via CLI:
```bash
hermes chat --toolsets web,file,terminal "Analyze this codebase"
```

### 8.2 Subagent Delegation (`delegate_task`)
Hermes can spawn concurrent, isolated subagents:
- **Zero-Context Isolation**: Subagents begin with empty conversational contexts to avoid prompt bloat and hallucination.
- **Explicit Parameter Passing**: The parent agent passes explicit instructions, target paths, and constraints in the delegate payload.
- **Tool Restriction**: Subagents inherit the parent's enabled toolsets, but interactive tools like `clarify` and `send_message` are restricted to prevent user confusion.

### 8.3 Python RPC Tool Calling
For multi-step pipelines (e.g. processing 50 files or batch-checking APIs), Hermes writes a Python script that calls tools via RPC. This executes in a single turn at **zero context-window cost**, returning only the final consolidated output.

---

## 9. Scheduled Automations (Cron)

Hermes includes a built-in cron engine managed by the gateway daemon:
- **Gateway Cron Runner**: Ticks every 60 seconds to inspect scheduled tasks.
- **Creation**: Use the `/cron` slash command or the `cronjob` tool.
- **Script-Only Mode**: Run standard shell/Python scripts on schedule without invoking an LLM, delivering stdout directly to your messaging platform (Telegram/Discord).
- **Commands**:
  ```bash
  hermes cron list      # Inspect all scheduled tasks
  hermes cron status    # Check daemon health
  ```

---

## 10. Operational Runbook & Production Best Practices

1. **VPS Deployment**:
   - Run under a dedicated system user (e.g. `radar` or `hermes`).
   - Use `hermes gateway install` to generate and enable systemd units.
   - Load environment variables via an explicit `.env` file referenced in the service definition.
2. **Messaging Best Practices**:
   - **Do not dump raw company lists or database dumps into Telegram/Discord.** Messaging platforms are control panels and ping receivers.
   - Return clean summary counts, execution statuses, and links to web dashboards.
3. **Deterministic Enforcement**:
   - Use CLI gates (e.g. `publish-check`, `doctor`, `today-qa`) before publishing or notifying users.
   - Let deterministic code score, filter, and validate data; use Hermes to orchestrate, analyze prose, and handle edge cases.
4. **Memory Optimization**:
   - When sessions run continuously, call `/new` at milestone completions to flush context into SQLite FTS5 and keep dialectic recall sharp.
