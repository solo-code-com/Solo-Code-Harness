# Solo-Code Harness — Hook System

> Hệ thống lifecycle hooks cho AI coding agent. Ported từ ECC v2.0.0-rc.1, tối ưu cho Kilo Code.

## Architecture

```
.kilo/hooks/
├── hooks.json              # Hook registry & profiles
├── run-with-flags.js       # Dispatcher cũ — KHÔNG được nối vào hooks.json (dead code)
├── pre-tool-use/           # Chạy TRƯỚC khi tool thực thi
│   ├── gate-guard.js           # Chặn lệnh bash phá hủy + secret trong command
│   ├── secret-scan.js          # Quét hardcoded secrets trong nội dung ghi
│   ├── config-protection.js    # Chặn sửa config linter/formatter
│   ├── executor-mode.js        # Gate ghi: orchestrator không tự Edit/Write
│   └── governance-capture.js   # Ghi log governance (chỉ profile `strict`)
├── post-tool-use/          # Chạy SAU khi tool hoàn thành
│   ├── quality-gate.js         # Kiểm tra format/lint (advisory)
│   ├── console-log-check.js    # Phát hiện debug statements
│   ├── context-monitor.js      # Theo dõi context & tool loops (advisory)
│   ├── edit-accumulator.js     # Ghi nhận file đã edit
│   ├── memory-manager.js       # Giới hạn kích thước memory
│   ├── check-output.js         # Đọc stderr, gợi ý sửa
│   └── security-check-post.js  # Quét secret trong git diff sau commit
├── hookify/                # hookify-engine.js — rule engine đọc .kilo/hookify/*.md
├── ralph/                  # ralph-loop.js (vòng lặp có ngân sách) + verify-gates.js
├── learnings/              # learn.js — log kết quả tool, tóm tắt cuối phiên
└── session/                # Session lifecycle
    ├── session-start.js    # Bootstrap session
    └── session-end.js      # Persist & summary
```

## Hook Profiles

| Profile  | Description |
|----------|------------|
| `minimal` | Chỉ gate-guard (an toàn tối thiểu) |
| `standard` | Tất cả hooks trừ governance-capture |
| `strict` | Tất cả hooks, gồm governance-capture |

Cấu hình trong `hooks.json` → `profiles`.

## Cách dùng

```bash
# Chạy hook thủ công
echo '{"tool_name":"Bash","tool_input":{"command":"rm -rf /tmp"}}' | node .kilo/hooks/pre-tool-use/gate-guard.js
echo $?  # 2 = BLOCKED

# gate-guard không có bypass — cố ý. Muốn tắt, sửa profile trong hooks.json,
# đừng thêm biến môi trường (không biến nào được đọc).

# governance-capture bật mặc định; SOLOCODE_GOVERNANCE=0 để tắt
echo '{"tool_name":"Bash","tool_input":{"command":"ls"}}' | SOLOCODE_GOVERNANCE=0 node .kilo/hooks/pre-tool-use/governance-capture.js
```

## State Files

- `.kilo/state/tool-count.json` — Đếm tool calls trong session
- `.kilo/state/edited-files.json` — Danh sách file đã edit
- `.kilo/state/sessions/` — Session logs
- `.kilo/logs/governance-events.jsonl` — Governance event log
