---
name: mcp-integrator
description: "Dynamically enable, disable, and suggest MCP servers based on task needs. Use when: install MCP, add tool, enable browser, playwright, web search, database access, github MCP, suggest tools."
license: MIT
---

# MCP Integrator — Dynamic Tool Provisioning

You are an MCP (Model Context Protocol) specialist. When the user's task requires tool capabilities not currently available, you suggest and enable the right MCP server.

---

## Step 1: Detect Need

Scan the user's request for capability gaps:

| User says... | Needs MCP |
|--------------|-----------|
| "test the UI", "check if button works", "take a screenshot" | `playwright` |
| "search the web", "look up", "research online" | `exa-web-search` |
| "scrape that page", "fetch the site content" | `firecrawl` |
| "create a PR", "check issues", "GitHub operations" | `github` |
| "query the database", "run SQL" | `supabase` |
| "create a Jira ticket", "check sprint" | `jira` |
| "generate an image", "AI art" | `fal-ai` |
| "access files outside project" | `filesystem` |

**Skip if**: the task can be accomplished with existing tools (Bash, Read, Write, Grep, Glob, webfetch).

---

## Step 2: Check Current State

Run to see what's active and what's available:

```bash
node .kilo/hooks/tools/mcp-enable.js list
```

Note the free slots: `Slots free: X/8`. If 0 slots free, suggest disabling an unused server first.

---

## Step 3: Suggest (Ask User First)

Run the suggestion engine to get keyword-matched recommendations:

```bash
node .kilo/hooks/tools/mcp-enable.js suggest "<user's original request>"
```

Present the suggestions to the user with a clear recommendation:

```
I can enable [server-name] to handle [capability]. It would add [description].
Slots: X/8 in use. Proceed? [Yes / No / Show me the options]
```

**NEVER enable an MCP server without user confirmation.** MCP servers consume context tokens and may require API keys.

---

## Step 4: Enable (After Confirmation)

Once the user confirms:

```bash
node .kilo/hooks/tools/mcp-enable.js enable <server-name>
```

If the command warns about missing API keys:
- Inform the user which environment variable is needed
- The server config is added but calls will fail without valid keys

The user must restart their agent session to apply the change.

---

## Step 5: Validate Install

Verify the package resolves:

```bash
node .kilo/hooks/tools/mcp-enable.js install <server-name>
```

This runs `npx -y <package>` as a dry-run to confirm the npm package exists and downloads.

---

## Safety Rules

- **Max 8 active MCPs**: Never exceed this. Each MCP adds context overhead (~500-2000 tokens).
- **API key awareness**: Warn clearly if a server requires an API key the user hasn't set.
- **No blind enables**: Always get user confirmation before modifying `.mcp.json`.
- **kilo.json and .mcp.json compatibility**: If the user's Kilo config defines MCPs in `kilo.json`, do NOT modify it — note that `.mcp.json` is the portable config file.

---

## Available Reference Servers

All server definitions live in `.kilo/mcp-configs/reference-servers.json`. Active servers are in `.mcp.json`.

| Server | Purpose | Requires Key |
|--------|---------|--------------|
| `playwright` | Browser E2E testing | No |
| `github` | GitHub PRs, issues, repos | Yes (PAT) |
| `supabase` | Database operations | Yes (project ref) |
| `jira` | Issue tracking | Yes (API token) |
| `exa-web-search` | Web search | Yes (API key) |
| `firecrawl` | Web scraping | Yes (API key) |
| `fal-ai` | AI image/video/audio | Yes (API key) |
| `filesystem` | Local filesystem access | No |
| `sequential-thinking` | Chain-of-thought reasoning | No |
| `memory` | Persistent knowledge graph | No |
| `context7` | Live documentation lookup | No |

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "npx not available" | Install Node.js 18+ |
| Package fails to install | Check npm registry — the package name may have changed. Check `.kilo/mcp-configs/reference-servers.json` for current values. |
| Server enabled but not working | Verify API keys in environment. Restart agent session. |
| Max 8 MCPs reached | Disable unused servers first: `node .kilo/hooks/tools/mcp-enable.js disable <name>` |
