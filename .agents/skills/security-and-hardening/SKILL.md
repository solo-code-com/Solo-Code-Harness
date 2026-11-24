---
name: security-and-hardening
description: "Hardens code against vulnerabilities. Use when handling user input, authentication, data storage, or external integrations. Use when building any feature that accepts untrusted data, manages user sessions, or interacts with third-party services."
---

# Security and Hardening

## Overview

Security-first development practices for web applications. Treat every external input as hostile, every secret as sacred, and every authorization check as mandatory. Security isn't a phase — it's a constraint on every line of code that touches user data, authentication, or external systems.

## When to Use

- Building anything that accepts user input
- Implementing authentication or authorization
- Storing or transmitting sensitive data
- Integrating with external APIs or services
- Adding file uploads, webhooks, or callbacks
- Handling payment or PII data

## Process: Threat Model First

Controls bolted on without a threat model are guesses. Before hardening, spend five minutes thinking like an attacker:

1. **Map the trust boundaries.** Where does untrusted data cross into your system? HTTP requests, form fields, file uploads, webhooks, third-party APIs, message queues, and **LLM output**. Every boundary is attack surface.
2. **Name the assets.** What's worth stealing or breaking? Credentials, PII, payment data, admin actions, money movement.
3. **Run STRIDE over each boundary** — a quick lens, not a ceremony:

| Threat | Ask | Typical mitigation |
|---|---|---|
| **S**poofing | Can someone impersonate a user/service? | Authentication, signature verification |
| **T**ampering | Can data be altered in transit or at rest? | Integrity checks, parameterized queries, HTTPS |
| **R**epudiation | Can an action be denied later? | Audit logging of security events |
| **I**nformation disclosure | Can data leak? | Encryption, field allowlists, generic errors |
| **D**enial of service | Can it be overwhelmed? | Rate limiting, input size caps, timeouts |
| **E**levation of privilege | Can a user gain rights they shouldn't? | Authorization checks, least privilege |

4. **Write abuse cases next to use cases.** For each feature, ask "how would I misuse this?" — then make that your first test.

If you can't name the trust boundaries for a feature, you're not ready to secure it. This is OWASP **A04: Insecure Design** — most breaches begin in design, not code.

## The Three-Tier Boundary System

### Always Do (No Exceptions)

- **Validate all external input** at the system boundary (API routes, form handlers)
- **Parameterize all database queries** — never concatenate user input into SQL
- **Encode output** to prevent XSS (use framework auto-escaping, don't bypass it)
- **Use HTTPS** for all external communication
- **Hash passwords** with bcrypt/scrypt/argon2 (never store plaintext)
- **Set security headers** (CSP, HSTS, X-Frame-Options, X-Content-Type-Options)
- **Use httpOnly, secure, sameSite cookies** for sessions
- **Run `npm audit`** (or equivalent) before every release

### Ask First (Requires Human Approval)

- Adding new authentication flows or changing auth logic
- Storing new categories of sensitive data (PII, payment info)
- Adding new external service integrations
- Changing CORS configuration
- Adding file upload handlers
- Modifying rate limiting or throttling
- Granting elevated permissions or roles

### Never Do

- **Never commit secrets** to version control (API keys, passwords, tokens)
- **Never log sensitive data** (passwords, tokens, full credit card numbers)
- **Never trust client-side validation** as a security boundary
- **Never disable security headers** for convenience
- **Never use `eval()` or `innerHTML`** with user-provided data
- **Never store sessions in client-accessible storage** (localStorage for auth tokens)
- **Never expose stack traces** or internal error details to users

## OWASP Top 10 Prevention Patterns

These are prevention patterns, not a ranking. For the 2021 ordering, see the quick-reference table in `references/security-checklist.md`.

### Injection (SQL, NoSQL, OS Command)

```typescript
// BAD: SQL injection via string concatenation
const query = `SELECT * FROM users WHERE id = '${userId}'`;

// GOOD: Parameterized query
const user = await db.query('SELECT * FROM users WHERE id = $1', [userId]);

// GOOD: ORM with parameterized input
const user = await prisma.user.findUnique({ where: { id: userId } });
```

### Broken Authentication

```typescript
import { hash, compare } from 'bcrypt';
const hashedPassword = await hash(plaintext, 12);
// Session: httpOnly + secure + sameSite cookies, env-based secret
```

### Cross-Site Scripting (XSS)

```typescript
// BAD: element.innerHTML = userInput;
// GOOD: React auto-escapes — <div>{userInput}</div>
// If raw HTML needed: import DOMPurify; const clean = DOMPurify.sanitize(userInput);
```

### Broken Access Control

Always check authorization, not just authentication:
```typescript
app.patch('/api/tasks/:id', authenticate, async (req, res) => {
  const task = await taskService.findById(req.params.id);
  if (task.ownerId !== req.user.id) return res.status(403).json({ error: 'FORBIDDEN' });
  return res.json(await taskService.update(req.params.id, req.body));
});
```

### Security Headers & CORS

```typescript
import helmet from 'helmet'; app.use(helmet());
app.use(cors({ origin: process.env.ALLOWED_ORIGINS?.split(','), credentials: true }));
```

### Sensitive Data Exposure

```typescript
function sanitizeUser(user: UserRecord): PublicUser {
  const { passwordHash, resetToken, ...publicFields } = user; return publicFields;
}
```

### Server-Side Request Forgery (SSRF)

Any server fetch of a user-influenced URL must validate: https-only, allowlisted host, reject private/reserved IPs (especially `169.254.169.254` — cloud metadata, the #1 SSRF target), forbid redirects.

```typescript
import { lookup } from 'node:dns/promises'; import ipaddr from 'ipaddr.js';
const ALLOWED_HOSTS = new Set(['hooks.example.com']);
async function assertSafeUrl(raw: string): Promise<URL> {
  const url = new URL(raw);
  if (url.protocol !== 'https:') throw new Error('https only');
  if (!ALLOWED_HOSTS.has(url.hostname)) throw new Error('host not allowed');
  const addrs = await lookup(url.hostname, { all: true });
  if (addrs.some((a) => ipaddr.parse(a.address).range() !== 'unicast'))
    throw new Error('private/reserved IP');
  return url;
}
await fetch(await assertSafeUrl(req.body.webhookUrl), { redirect: 'error' });
// TOCTOU caveat: fetch resolves DNS again. For high-risk, use pinned IP or ssrf-req-filter.

## Input Validation

Validate at system boundaries with schema validation (e.g., zod). Reject unknown fields. Validate type, length, format, and range. For file uploads: restrict MIME types, cap size (5MB default), don't trust file extensions — check magic bytes if critical.

## npm audit Strategy

Critical/high + reachable → fix immediately. Moderate → next release. Low → track. Dev-only → convenience. Document deferrals with review dates. Commit lockfile, install with `npm ci` in CI.

### Supply-Chain Hygiene

`npm audit` catches CVEs but not malicious/typosquatted packages. Review new deps before adding, watch for `postinstall` scripts, avoid typosquats. Every dependency is attack surface.

## Rate Limiting

Apply general rate limits (100 req/15min for API) and stricter limits on auth endpoints (10 req/15min). Use `express-rate-limit` or equivalent.

## Secrets Management

`.env` files: `.env.example` committed (templates only), `.env`/`.env.local` never committed. Check before committing: `git diff --cached | grep -iE "password|secret|api_key|token"`. **If a secret is committed, rotate it immediately** — assume compromise, revoke key first, then purge history.

## Securing AI / LLM Features

Map to [OWASP Top 10 for LLM Applications (2025)](https://genai.owasp.org/llm-top-10/):

- **LLM01 Prompt Injection**: system prompt is not a security boundary. Enforce permissions in code.
- **LLM05 Output Handling**: treat model output as untrusted. Never pass into `eval`, SQL, shell, `innerHTML`, file paths.
- **LLM02/LLM07 Data Leakage**: keep secrets and cross-tenant data out of prompts.
- **LLM06 Excessive Agency**: scope tools, require confirmation for destructive actions.
- **LLM10 Unbounded Consumption**: cap tokens, rate, and loop depth.
- **LLM08 Vector/Embedding Weaknesses**: partition embeddings per tenant in RAG.

```typescript
// BAD: model output as command → sql injection / XSS via LLM
// GOOD: parse → validate schema → encode → act on allowlisted actions only
container.textContent = await llm.reply(userMessage);
```

## Security Review Checklist

- [ ] Passwords bcrypt/scrypt/argon2 (≥12 rounds); sessions httpOnly+secure+sameSite; login rate-limited
- [ ] Every endpoint checks authorization; users access only own resources
- [ ] All input validated at boundaries; SQL parameterized; output encoded; SSRF URLs validated
- [ ] No secrets in code; sensitive fields excluded from responses
- [ ] Security headers (CSP, HSTS); CORS restricted; deps audited; errors generic
- [ ] Lockfile committed; CI uses `npm ci`; new deps reviewed
- [ ] LLM output treated as untrusted; secrets kept out of prompts; tools scoped

For detailed pre-commit verification, see `references/security-checklist.md`.

## Common Rationalizations

| Rationalization | Reality |
|---|---|
| "This is an internal tool, security doesn't matter" | Internal tools get compromised. Attackers target the weakest link. |
| "We'll add security later" | Security retrofitting is 10x harder than building it in. Add it now. |
| "No one would try to exploit this" | Automated scanners will find it. Security by obscurity is not security. |
| "The framework handles security" | Frameworks provide tools, not guarantees. You still need to use them correctly. |
| "It's just a prototype" | Prototypes become production. Security habits from day one. |
| "Threat modeling is overkill here" | Five minutes of "how would I attack this?" prevents the design flaws no control can patch later. |
| "It's just LLM output, it's only text" | That "text" can be a SQL statement, a script tag, or a shell command. Treat it like any untrusted input. |

## Red Flags

- User input passed directly to database queries, shell commands, or HTML rendering
- Secrets in source code or commit history
- API endpoints without authentication or authorization checks
- Missing CORS configuration or wildcard (`*`) origins
- No rate limiting on authentication endpoints
- Stack traces or internal errors exposed to users
- Dependencies with known critical vulnerabilities
- Server fetches user-supplied URLs without an allowlist (SSRF)
- LLM/model output passed into a query, the DOM, a shell, or `eval`
- Secrets, PII, or the full system prompt placed inside an LLM context window

## Verification

After implementing security-relevant code:

- [ ] `npm audit` shows no critical or high vulnerabilities
- [ ] No secrets in source code or git history
- [ ] All user input validated at system boundaries
- [ ] Authentication and authorization checked on every protected endpoint
- [ ] Security headers present in response (check with browser DevTools)
- [ ] Error responses don't expose internal details
- [ ] Rate limiting active on auth endpoints
- [ ] Server-side URL fetches validated against an allowlist (no SSRF)
- [ ] LLM/model output validated and encoded before use (if AI features present)
