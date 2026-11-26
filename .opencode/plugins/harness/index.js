// harness plugin - the wrapper-independent part of the retired Claude hooks.
//
// Deliberately dependency-free: it exports a plain { id, setup } definition the
// V2 loader accepts ("must export a default definition with an id and an effect
// or setup function"), so it cannot fail because @opencode/plugin is missing.
// Every hook body is guarded: a harness bug must never block a session.

import { execFileSync } from "node:child_process"
import { appendFileSync, mkdirSync, writeFileSync } from "node:fs"
import { join } from "node:path"

const STATE_DIR = ".solocode"

function note(directory, line) {
  try {
    mkdirSync(join(directory, STATE_DIR), { recursive: true })
    appendFileSync(join(directory, STATE_DIR, "harness-plugin.log"), `${new Date().toISOString()} ${line}\n`)
  } catch {
    // logging is best effort
  }
}

// A branch name or a dirty-path listing is attacker-influenced input once it is
// pasted into a system block, so only a conservative alphabet survives.
function safe(value) {
  return String(value).replace(/[^A-Za-z0-9._\/ -]/g, "_")
}

function git(directory, args) {
  try {
    return execFileSync("git", ["-C", directory, ...args], { encoding: "utf8", windowsHide: true }).trim()
  } catch {
    return "-"
  }
}

export default {
  id: "harness",
  setup(ctx) {
    const directory = ctx?.location?.directory ?? process.cwd()
    note(directory, `setup ok (opencode ${ctx?.app?.version ?? "unknown"})`)

    // Replaces the session_start hook: give every model call the branch, the
    // short sha and the dirty count, which is what the old hook injected.
    try {
      ctx.session.hook("context", (event) => {
        const branch = git(directory, ["rev-parse", "--abbrev-ref", "HEAD"])
        const sha = git(directory, ["rev-parse", "--short", "HEAD"])
        const dirty = git(directory, ["status", "--porcelain"]).split("\n").filter(Boolean).length
        event.system.push({
          type: "text",
          text: `<harness state>branch=${safe(branch)} sha=${safe(sha)} dirty=${dirty}</harness state>`,
        })
      })
    } catch (error) {
      note(directory, `context hook unavailable: ${error}`)
    }

    // Replaces the pre_compact hook: leave a checkpoint the next session can read.
    try {
      ctx.session.hook("compaction", (event) => {
        try {
          const checkpoint = {
            at: new Date().toISOString(),
            branch: git(directory, ["rev-parse", "--abbrev-ref", "HEAD"]),
            sha: git(directory, ["rev-parse", "--short", "HEAD"]),
            messages: Array.isArray(event?.messages) ? event.messages.length : null,
          }
          writeFileSync(join(directory, STATE_DIR, "context-checkpoint.json"), JSON.stringify(checkpoint, null, 2))
          note(directory, "compaction checkpoint written")
        } catch (error) {
          note(directory, `checkpoint failed: ${error}`)
        }
      })
    } catch (error) {
      note(directory, `compaction hook unavailable: ${error}`)
    }

    return () => note(directory, "unloaded")
  },
}