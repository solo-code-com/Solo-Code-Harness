# Live Documentation Sources

This file contains WebFetch URLs for fetching current information from platform.claude.com and Agent SDK repositories. Use these when users need the latest data that may have changed since the cached content was last updated.

## When to Use WebFetch

- User explicitly asks for "latest" or "current" information
- Cached data seems incorrect
- User asks about features not covered in cached content
- User needs specific API details or examples

## Claude API Documentation URLs

### Models & Pricing

| Topic           | URL                                                                          | Extraction Prompt                                                               |
| --------------- | ---------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| Models Overview | `https://platform.claude.com/docs/en/about-claude/models/overview.md`        | "Extract current model IDs, context windows, and pricing for all Claude models" |
| Migration Guide | `https://platform.claude.com/docs/en/about-claude/models/migration-guide.md` | "Extract breaking changes, deprecated parameters, and per-model migration steps when moving to a newer Claude model" |
| Introducing Claude Fable 5 | `https://platform.claude.com/docs/en/about-claude/models/introducing-claude-fable-5.md` | "Extract capabilities, API changes, and availability stages for Claude Fable 5 and Claude Mythos 5" |
| Pricing         | `https://platform.claude.com/docs/en/pricing.md`                             | "Extract current pricing per million tokens for input and output"               |
| Cost Optimization | `https://platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence.md` | "Extract measured cost levers, cache and batch savings, effort and model cost-per-task comparisons, budget controls, and multi-model guidance" |

### Core Features

| Topic             | URL                                                                          | Extraction Prompt                                                                      |
| ----------------- | ---------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| Extended Thinking | `https://platform.claude.com/docs/en/build-with-claude/extended-thinking.md` | "Extract extended thinking parameters, budget_tokens requirements, and usage examples" |
| Adaptive Thinking | `https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking.md` | "Extract adaptive thinking setup, effort levels, and Claude Opus 5 usage examples"         |
| Effort Parameter  | `https://platform.claude.com/docs/en/build-with-claude/effort.md`            | "Extract effort levels, cost-quality tradeoffs, and interaction with thinking"        |
| Tool Use          | `https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview.md`  | "Extract tool definition schema, tool_choice options, and handling tool results"       |
| Streaming         | `https://platform.claude.com/docs/en/build-with-claude/streaming.md`         | "Extract streaming event types, SDK examples, and best practices"                      |
| Prompt Caching    | `https://platform.claude.com/docs/en/build-with-claude/prompt-caching.md`    | "Extract cache_control usage, pricing benefits, and implementation examples"           |

### Media & Files

| Topic       | URL                                                                    | Extraction Prompt                                                 |
| ----------- | ---------------------------------------------------------------------- | ----------------------------------------------------------------- |
| Vision      | `https://platform.claude.com/docs/en/build-with-claude/vision.md`      | "Extract supported image formats, size limits, and code examples" |
| PDF Support | `https://platform.claude.com/docs/en/build-with-claude/pdf-support.md` | "Extract PDF handling capabilities, limits, and examples"         |

### API Operations

| Topic            | URL                                                                         | Extraction Prompt                                                                                       |
| ---------------- | --------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| Batch Processing | `https://platform.claude.com/docs/en/build-with-claude/batch-processing.md` | "Extract batch API endpoints, request format, and polling for results"                                  |
| Files API        | `https://platform.claude.com/docs/en/build-with-claude/files.md`            | "Extract file upload, download, referencing in messages, supported types, and the migration steps from files-api-2025-04-14" |
| Token Counting   | `https://platform.claude.com/docs/en/build-with-claude/token-counting.md`   | "Extract token counting API usage and examples"                                                         |
| Rate Limits      | `https://platform.claude.com/docs/en/api/rate-limits.md`                    | "Extract current rate limits by tier and model"                                                         |
| Usage and Cost Admin API | `https://platform.claude.com/docs/en/manage-claude/usage-cost-api.md` | "Extract the usage_report and cost_report endpoints, Admin API key requirements, filter and group_by dimensions, token fields, and granularity limits" |
| Errors           | `https://platform.claude.com/docs/en/api/errors.md`                         | "Extract HTTP error codes, meanings, and retry guidance"                                                |
| Amazon Bedrock   | `https://platform.claude.com/docs/en/build-with-claude/claude-on-amazon-bedrock.md` | "Extract the AnthropicBedrockMantle client per language, `anthropic.`-prefixed model IDs, auth paths, feature availability, and regions" |
| Claude Platform on AWS | `https://platform.claude.com/docs/en/build-with-claude/claude-platform-on-aws.md` | "Extract the AnthropicAWS client per language, SigV4 auth, credential precedence, short-term API keys, workspace_id, and region requirements" |
| Claude Platform on AWS - IAM actions | `https://platform.claude.com/docs/en/api/claude-platform-on-aws-iam-actions.md` | "Extract the IAM action names, resource ARNs, and policy examples required for each API capability" |

### Admin API (Organization Management)

| Topic                | URL                                                                     | Extraction Prompt                                                                     |
| -------------------- | ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| Admin API Guide      | `https://platform.claude.com/docs/en/manage-claude/admin-api.md`        | "Extract Admin API authentication, SDK/CLI usage, and member/invite/key management"   |
| Admin API Reference  | `https://platform.claude.com/docs/en/api/admin.md`                      | "Extract endpoint parameters, responses, and pagination for the Admin API"            |
| Workspaces           | `https://platform.claude.com/docs/en/manage-claude/workspaces.md`       | "Extract workspace create/list/archive and member management via API"                  |
| Rate Limits API      | `https://platform.claude.com/docs/en/manage-claude/rate-limits-api.md`  | "Extract org and workspace rate limit report endpoints and filters"                    |
| WIF Admin            | `https://platform.claude.com/docs/en/manage-claude/wif-admin-api.md`    | "Extract service account, federation issuer, and federation rule management"           |
| Usage & Cost Reports | `https://platform.claude.com/docs/en/manage-claude/usage-cost-api.md`   | "Extract usage and cost report endpoints (curl-only, not in the SDKs)"                 |

### Tools

| Topic          | URL                                                                                    | Extraction Prompt                                                                        |
| -------------- | -------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| Code Execution | `https://platform.claude.com/docs/en/agents-and-tools/tool-use/code-execution-tool.md` | "Extract code execution tool setup, file upload, container reuse, and response handling" |
| Computer Use   | `https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use.md`        | "Extract computer use tool setup, capabilities, and implementation examples"             |
| Bash Tool      | `https://platform.claude.com/docs/en/agents-and-tools/tool-use/bash-tool.md`           | "Extract bash tool schema, reference implementation, and security considerations"        |
| Text Editor    | `https://platform.claude.com/docs/en/agents-and-tools/tool-use/text-editor-tool.md`    | "Extract text editor tool commands, schema, and reference implementation"                |
| Memory Tool    | `https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool.md`         | "Extract memory tool commands, directory structure, and implementation patterns"         |
| Tool Search    | `https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool.md`    | "Extract tool search setup, when to use, and cache interaction"                          |
| Programmatic Tool Calling | `https://platform.claude.com/docs/en/agents-and-tools/tool-use/programmatic-tool-calling.md` | "Extract PTC setup, script execution model, and tool invocation from code"    |
| Skills         | `https://platform.claude.com/docs/en/agents-and-tools/skills.md`                       | "Extract skill folder structure, SKILL.md format, and loading behavior"                  |
| Skills Guide   | `https://platform.claude.com/docs/en/build-with-claude/skills-guide.md`                | "Extract the Skills API (/v1/skills) usage and the migration steps from skills-2025-10-02" |

### Advanced Features

| Topic              | URL                                                                           | Extraction Prompt                                   |
| ------------------ | ----------------------------------------------------------------------------- | --------------------------------------------------- |
| Structured Outputs | `https://platform.claude.com/docs/en/build-with-claude/structured-outputs.md` | "Extract output_config.format usage and schema enforcement"                           |
| Compaction         | `https://platform.claude.com/docs/en/build-with-claude/compaction.md`         | "Extract compaction setup, trigger config, and streaming with compaction"             |
| Context Editing    | `https://platform.claude.com/docs/en/build-with-claude/context-editing.md`    | "Extract context editing thresholds, what gets cleared, and configuration"            |
| Citations          | `https://platform.claude.com/docs/en/build-with-claude/citations.md`          | "Extract citation format and implementation"        |
| Context Windows    | `https://platform.claude.com/docs/en/build-with-claude/context-windows.md`    | "Extract context window sizes and token management" |

### Anthropic CLI

The `ant` CLI provides terminal access to the Claude API. Every API resource is exposed as a subcommand. It is the recommended way to create agents and environments from version-controlled YAML (`ant beta:agents create < agent.yaml` - see `shared/anthropic-cli.md`), and also exposes sessions and every other API resource for scripting and interactive inspection.

| Topic         | URL                                                     | Extraction Prompt                                                                                  |
| ------------- | ------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| Anthropic CLI | `https://platform.claude.com/docs/en/api/sdks/cli.md`   | "Extract CLI install, authentication, command structure, and the beta:agents/environments/sessions commands" |
| Authentication overview | `https://platform.claude.com/docs/en/manage-claude/authentication.md` | "Extract the credential options (API keys, interactive OAuth login, Workload Identity Federation) and when to use each" |
| WIF reference | `https://platform.claude.com/docs/en/manage-claude/wif-reference.md`  | "Extract credential precedence order, the profile configuration file schema, and the configuration directory layout" |

---

## Claude API SDK Repositories

WebFetch these when a binding (class, method, namespace, field) isn't covered in the cached `{lang}/` skill files.

| SDK        | URL                                                      | Extraction Prompt                                                                                     |
| ---------- | -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| Python     | `https://github.com/anthropics/anthropic-sdk-python`     | "Extract the classes and method signatures for the requested feature or resource"                      |
| TypeScript | `https://github.com/anthropics/anthropic-sdk-typescript` | "Extract the classes and method signatures for the requested feature or resource"                      |
| Java       | `https://github.com/anthropics/anthropic-sdk-java`       | "Extract the classes, builders, and method signatures for the requested feature or resource"           |
| Go         | `https://github.com/anthropics/anthropic-sdk-go`         | "Extract the types and method signatures for the requested feature or resource"                        |
| Ruby       | `https://github.com/anthropics/anthropic-sdk-ruby`       | "Extract the methods and parameter shapes for the requested feature or resource"                        |
| C#         | `https://github.com/anthropics/anthropic-sdk-csharp`     | "Extract the classes and method signatures for the requested feature or resource (NuGet package)"      |
| PHP        | `https://github.com/anthropics/anthropic-sdk-php`        | "Extract the classes and method signatures for the requested feature or resource"                       |

Each SDK repo also ships runnable programs under `examples/` - including the refusal-fallback / `fallbacks` examples (client-side middleware registration, fallback state, server-side `fallbacks` param). Fetch those for exact per-language syntax instead of translating another language's example.

### SDK major-version upgrade guides

Authoritative change lists for upgrading the SDK package itself across a major version. The bundled `{lang}/claude-api/sdk-upgrade.md` is the executable form; when the two disagree, the repository guide wins.

| SDK                | URL                                                                         | Extraction Prompt                                                                                                   |
| ------------------ | --------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| Python (0.x -> 1.x) | `https://github.com/anthropics/anthropic-sdk-python/blob/main/MIGRATION.md` | "Extract every breaking change with its before/after code, the new minimum Python version, and the upgrade command" |

---

## Fallback Strategy

If WebFetch fails (network issues, URL changed):

1. Use cached content from the language-specific files (note the cache date)
2. Inform user the data may be outdated
3. Suggest they check platform.claude.com or the GitHub repos directly
