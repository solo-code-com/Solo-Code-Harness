---
description: Review a change with independent reviewers, then record a receipt for this exact tree.
---
# /review — Independent Review, With a Receipt

Run an independent review over the current change set, then leave proof that it
happened. A review nobody can point at afterwards is indistinguishable from no
review at all.

The receipt proves a review was **recorded** for this tree. It does not prove
the review was good, and the reviewer and model labels it stores are
declarative — nothing verifies that those agents actually ran.

## Procedure

1. **Get the diff.** Execute `git diff HEAD --stat`, then `git diff HEAD`. Read
   the entire patch before judging any part of it. The change set is everything
   against HEAD, so staged and unstaged tracked changes both count.

2. **Run the repo gates** and keep the raw output:
   - `python .github/scripts/security_scan.py .`
   - `ruff check .`
   - `python tools/garden.py`

3. **Dispatch independent reviewers.** Send `code-reviewer` the diff plus the
   task description. Add `security-auditor` when the diff touches auth,
   authentication, secrets, `.env` handling, config, or dependencies
   (`package.json`, `pyproject.toml`, `requirements.txt`, lockfiles). One
   reviewer per perspective. The author's own summary is not a review.

4. **Demand evidence, not confidence.** Every finding returns in this shape:

   | Claim | Command run | Output |
   |---|---|---|

   A finding with no command behind it is a hypothesis, and the report must say
   so. Do not accept "Confident: yes" as evidence.

5. **Address or record the findings.** Fix what blocks the change and note what
   was deliberately left unfixed, with the reason. Do not rewrite a reviewer's
   output to look cleaner than it was.

6. **Record the receipt** with the reviewer and model actually used:
   `python tools/review_receipt.py record --reviewer codex --model gpt-6.1-sol`
   It writes one file per tree hash under the policy's `receipt_dir`,
   `.solocode/reviews` by default, and prints the path.

## What blocks and what does not

This command blocks nothing by itself — it is a procedure, not a gate, and a
change can be committed without ever running it. The gate is
`python tools/review_receipt.py verify`: it exits 1 when the change set meets
both policy thresholds and no receipt exists for that tree, and 2 when the
environment itself is broken (git unavailable, not a repo, unreadable policy).
Run `verify` before the change lands, not after.

Thresholds live in `tools/config/review-policy.json` — 2 files and 30 changed
lines by default, so a one-file fixup stays exempt. A receipt written for a
tree you have since edited does not count: edit the diff, and the hash moves.
