# DSH Solo Plugins

External plugins that bring Solo-Code operating practices to DeepSeek Harness without modifying its kernel.

## Included packages

- `@solo-code/dsh-solo-policy`: balanced approval policy for destructive commands, dependency changes, network access, Git publication, file writes, and credential files.
- `@solo-code/dsh-solo-guidance`: model-invocable planning, debugging, review, and verification skills.

DeepSeek Harness already supplies durable sessions, goals, plans, workflows, subagents, sandboxing, approval, repeat-call detection, and provider adapters. This workspace configures and extends those seams; it does not recreate them.

## Provider profiles

Copy one profile patch into a custom DSH profile after installing this workspace's packages with `dsh plugin --profile <name> add <path>`. Keep `DEEPSEEK_API_KEY` and `OPENROUTER_API_KEY` in the process environment or DSH credential store.

- `profiles/direct-deepseek.cordis.patch.yml`
- `profiles/openrouter-deepseek.cordis.patch.yml`

OpenRouter uses DSH's included `@deepseek-ai/dsh-llm-pi-ai` adapter with the `openai-completions` protocol. Change only the selected model id after confirming it is available in your OpenRouter account.

## Validation

Run `pnpm test` from this directory. The policy tests do not require a model key or network access.
