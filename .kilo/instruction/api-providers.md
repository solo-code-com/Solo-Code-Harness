# API Providers - DeepSeek & OpenCode

> Auto-loaded when the agent needs to call external LLM APIs or switch between model providers.

## Overview

This project supports two external LLM API providers beyond the primary Copilot model:

| Provider | API Type | Base URL Env | Key Env |
|----------|----------|-------------|---------|
| **DeepSeek** | OpenAI-compatible | `DEEPSEEK_BASE_URL` | `DEEPSEEK_API_KEY` |
| **OpenRouter** | OpenAI-compatible proxy | fixed (`_OPENROUTER_BASE_URL`) | `OPENROUTER_API_KEY` |

Both providers expose OpenAI-compatible `/v1/chat/completions` endpoints. OpenRouter proxies multiple models (DeepSeek, Claude, GPT, Gemini, Qwen, Kimi, GLM, MiniMax, Step) through a single API key.

> The `opencode-go/...` and `opencode/...` models used by the OpenCode CLI are not declared in this repo. They resolve through OpenCode's own global provider config (`~/.config/opencode/opencode.json`), so no key for them belongs in `.env`.

> Model IDs drift. Providers add and retire models without notice, so treat
> `GET {DEEPSEEK_BASE_URL}/models` as the source of truth and re-verify before
> using an ID this file does not list.

## Environment Configuration

Set these environment variables before use. Never hardcode keys in source files.

```powershell
# DeepSeek direct
[Environment]::SetEnvironmentVariable("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1", "User")
[Environment]::SetEnvironmentVariable("DEEPSEEK_API_KEY", "sk-your-deepseek-key", "User")

# OpenRouter (endpoint is fixed in tools/opencode_engine.py)
[Environment]::SetEnvironmentVariable("OPENROUTER_API_KEY", "sk-or-your-key", "User")
```

After setting, restart VS Code for Copilot to pick up the new variables.

## Model Selection

### Available Models via DeepSeek Direct

The harness declares a self-contained `deepseek` provider in
`.opencode/opencode.json`, generated from `_DEEPSEEK_MODELS` in
`tools/opencode_engine.py` (base URL `${DEEPSEEK_BASE_URL}`, key
`DEEPSEEK_API_KEY`, both from `.env`). The `.env.template` ships
`https://api.deepseek.com/v1`.

Verified 2026-10-06 against `GET {DEEPSEEK_BASE_URL}/models`: the official API
serves two ids, both reporting `context_window` 1,048,576 and
`max_output_tokens` 393,216.

| Model ID | Reported name | Best For |
|----------|---------------|----------|
| `deepseek-flash` | DeepSeek-V4.1-Flash | DeepSeek V4.1 Flash on the direct API |
| `deepseek-v4-pro` | DeepSeek-V4-Pro | Flagship, deep reasoning |

> The direct API names V4.1 Flash **`deepseek-flash`**. The id
> `deepseek-v4.1-flash` exists on OpenRouter and through the OpenCode CLI
> (`opencode-go/deepseek-v4.1-flash`).

### Available Models via OpenRouter

The harness declares a self-contained `openrouter` provider (`_OPENROUTER_MODELS`,
base URL `https://openrouter.ai/api/v1`, key `OPENROUTER_API_KEY`). DeepSeek ids
verified against `GET https://openrouter.ai/api/v1/models` (465 models,
2026-10-06); the V4 family reports a 1,048,576-token context. `:batch` and
`~`-prefixed alias ids are excluded.

| Model ID | Best For |
|----------|----------|
| `deepseek/deepseek-v4.1-flash` | The extra route to DeepSeek V4.1 Flash |
| `deepseek/deepseek-v4-pro` | Flagship, deep reasoning |
| `deepseek/deepseek-v4-flash` | Fast, cheap |
| `deepseek/deepseek-v4-flash-vision-exp` | Multimodal (experimental) |

## API Usage Patterns

### Calling DeepSeek API (OpenAI-compatible)

Only usable when `DEEPSEEK_BASE_URL` / `DEEPSEEK_API_KEY` are set.

```python
import os
import httpx

async def call_deepseek(prompt: str, system: str = "", model: str = "deepseek-chat") -> str:
    """Call DeepSeek chat completions API."""
    base_url = os.environ["DEEPSEEK_BASE_URL"]
    api_key = os.environ["DEEPSEEK_API_KEY"]

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 4096,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]
```

## Copilot Chat Model Switching

When using Copilot Chat in VS Code, switch the active model via:
- **Command Palette** (`Ctrl+Shift+P`) -> `GitHub Copilot: Switch Model`
- Or click the model name in the Copilot Chat header

Configured models are defined in `.vscode/settings.json` under `github.copilot.chat.models`.

## When to Use Which Provider

| Scenario | Provider | Model |
|----------|----------|-------|
| **Architecture design** | OpenCode | `opencode-go/claude-opus-5` or `opencode-go/claude-sonnet-5` |
| **Code review** | OpenCode | `opencode-go/claude-sonnet-5` or `opencode-go/gpt-5.5` |
| **Harness default (OpenCode / Kilo orchestrator)** | OpenCode | `opencode-go/deepseek-v4-pro` |
| **Free-tier worker default** | OpenCode | `opencode-go/beam-free` |
| **Refactoring** | OpenCode | `opencode-go/deepseek-v4-pro` |
| **Test generation** | OpenCode | `opencode-go/gpt-5.4` |
| **Complex algorithms / math** | OpenCode | `opencode-go/deepseek-v4-pro` |
| **Quick edits / completions** | OpenCode | `opencode-go/deepseek-v4.1-flash` or `opencode-go/gpt-5.4-mini` |
| **Cost-sensitive batch work** | OpenCode | `opencode-go/deepseek-v4.1-flash` |
| **Long-context analysis** | OpenCode | `opencode-go/gemini-3.5-flash` or `opencode-go/kimi-k3` |
| **Direct DeepSeek API** | DeepSeek | `deepseek-flash` or `deepseek-v4-pro` |

## Error Handling

```python
import httpx

async def safe_api_call(fn, *args, **kwargs):
    """Wrapper with retry and error handling."""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            return await fn(*args, **kwargs)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                # Rate limited - exponential backoff
                await asyncio.sleep(2 ** attempt)
                continue
            if e.response.status_code == 401:
                raise RuntimeError("Invalid API key - check environment variables")
            if e.response.status_code >= 500:
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
            raise
        except httpx.TimeoutException:
            if attempt < max_retries - 1:
                continue
            raise RuntimeError("API call timed out after retries")
    raise RuntimeError("API call failed after max retries")
```

## Security Rules

- **Never hardcode API keys** - always use environment variables
- **Never log API responses** that contain generated code or sensitive data
- **Validate response structure** before accessing `choices[0].message.content`
- **Set reasonable timeouts** - 120s for chat completions, 30s for embeddings
- **Rotate keys periodically** - regenerate provider keys on a schedule and after any leak
