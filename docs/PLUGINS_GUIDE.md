# DeepSeek Harness - Plugins Guide

## Overview

DeepSeek Harness có 53 packages trong monorepo.

## Core Packages

### Execution
- agent-loop - Agent execution
- llm - Multi-provider LLM
- tools - Tool execution
- session - Session management

### Environment
- shell - Shell execution
- subprocess - Process spawning
- ssh - SSH remote
- sandbox - Confinement
- terminal - Terminal sessions

### Storage
- fs - Filesystem
- storage - State persistence
- document - Documents
- attachment - File attachments

### AI Capabilities
- browser-use - Browser automation
- computer-use - Computer control
- lsp - Language Server Protocol
- mcp - Model Context Protocol
- context - Context management
- compaction - Context compaction

### Development
- workflow - Workflow orchestration
- subagent - Subagent delegation
- plan - Planning
- skill - Skills
- goal - Goals
- todo - Tasks

### NEW Plugin
- solo-code - Multi-engine orchestration (COMPLETED)

## Installation Methods

### Method 1: Profile Configuration (Recommended)

Location: ~/.dsh/profiles/web/cordis.patch.yml

Example for solo-code:

```yaml
- $patch: true
  $target: plugins
  $append:
    - name: solo-code
      config:
        executors:
          opencode:
            command: python tools/opencode_delegate.py
            model: commandcode/deepseek-v4-pro
        default_executor: opencode
        guards:
          executor_mode: on
        tracking:
          enabled: true
```

Start with profile:
```bash
dsh --profile web
```

### Method 2: Bundle Configuration

Location: packages/bundle/base/cordis.yml

Add plugin to bundle config.

### Method 3: Programmatic

```typescript
import { Context } from '@deepseek-ai/cordis'
import { SoloCodePlugin } from '@deepseek-ai/dsh-solo-code'

const ctx = new Context()
ctx.plugin(SoloCodePlugin, config)
await ctx.start()
```

### Method 4: CLI Arguments

```bash
dsh --plugin solo-code --config ./config.yml
```

## Profile Structure

```
~/.dsh/profiles/
  web/
    cordis.yml              # Base config
    cordis.patch.yml        # Patches
  cli/
    cordis.yml
  custom/
    cordis.yml
```

## Creating Custom Profile

```bash
# Create directory
mkdir -p ~/.dsh/profiles/my-profile

# Copy base
cp ~/.dsh/profiles/web/cordis.yml ~/.dsh/profiles/my-profile/

# Create patch
cat > ~/.dsh/profiles/my-profile/cordis.patch.yml << EOF
- \$patch: true
  \$target: plugins
  \$append:
    - name: solo-code
      config:
        default_executor: opencode
EOF

# Use it
dsh --profile my-profile
```

## Plugin Patterns

### Enable/Disable

Disable:
```yaml
- $patch: true
  $target: plugins
  $remove:
    - name: telemetry
```

Enable:
```yaml
- $patch: true
  $target: plugins
  $append:
    - name: browser-use
      config:
        headless: true
```

### Override Config

```yaml
- $patch: true
  $target: plugins
  $modify:
    - name: llm
      config:
        providers:
          deepseek:
            model: deepseek-chat
```

## Common Configurations

### MCP Tools

```yaml
- name: mcp
  config:
    servers:
      filesystem:
        command: npx
        args:
          - -y
          - @modelcontextprotocol/server-filesystem
```

### Browser Use

```yaml
- name: browser-use
  config:
    headless: false
    viewport:
      width: 1280
      height: 720
```

### SSH

```yaml
- name: ssh
  config:
    hosts:
      production:
        host: prod.example.com
```

## Verification

Check loaded plugins:
```bash
dsh --profile web --verbose
```

Test programmatically:
```typescript
const plugins = Object.keys(ctx)
console.log('Active:', plugins)
```

## Troubleshooting

1. Check profile path exists
2. Validate YAML syntax
3. Check plugin package exists
4. Review logs with --verbose

## Plugin Development

Create new plugin:

1. Create package structure
2. Setup package.json
3. Implement Cordis Service
4. Build and test

Example:
```typescript
import { Context, Service } from '@deepseek-ai/cordis'

export class MyPlugin extends Service {
  constructor(ctx: Context, config: any) {
    super(ctx, 'my-plugin', true)
  }
}

export default MyPlugin
```

## Resources

- docs/architecture.md
- docs/cookbook/extension-cookbook.md
- packages/ - 53 plugin examples
- packages/solo-code/ - NEW multi-engine plugin

## Summary

- 53 packages available
- Profile-based config recommended
- YAML patch system
- Easy to extend
- Solo-code plugin ready for use
