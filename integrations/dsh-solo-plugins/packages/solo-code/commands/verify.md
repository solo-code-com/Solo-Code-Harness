---
description: Run verification to prove code works by executing it, not just inspecting.
---
# /verify — Prove It Works

## Purpose
Prove code changes work by running the application and observing behavior. Go beyond static analysis — actually execute.

## Behavior
1. **Build** — Does it compile/build without errors?
2. **Run** — Start the app. Does it launch?
3. **Exercise** — Trigger the changed code path
4. **Assert** — Verify the expected output/behavior
5. **Edge cases** — Test boundaries (empty input, null, max values)

## Rules
- Never mark a task "done" without running the code
- If the app can't be started, say so — don't guess
- Check console/logs for errors even if UI looks fine
- Compare behavior before and after the change
