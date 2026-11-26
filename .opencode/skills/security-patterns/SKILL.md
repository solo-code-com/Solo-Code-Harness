---
name: "security-patterns"
description: "When editing auth, controllers, middleware, config, or `.env` files, follow these security rules:"
---

# Security Patterns

When editing auth, controllers, middleware, config, or `.env` files, follow these security rules:

## Authentication & Authorization

- Validate auth tokens BEFORE any business logic
- Never store credentials in code or config files — use environment variables or a secrets manager
- Apply principle of least privilege to all role checks
- Session tokens must use `httpOnly`, `secure`, and `SameSite=Strict` cookies
- JWT tokens must have expiration, use RS256/HS256 minimum, and never accept `alg: none`

## Input Validation

- ALL user input is untrusted — validate type, length, format, and range
- Use parameterized queries for SQL — never string interpolation
- Sanitize output for the target context (HTML encode for web, shell escape for CLI)
- File uploads: validate MIME type, size limit, and sanitize filenames

## Cryptography

- Use bcrypt/scrypt/argon2 for password hashing — never MD5/SHA1
- Use `crypto.randomBytes()` or equivalent for token generation — never `Math.random()`
- AES-256-GCM for symmetric encryption; never ECB mode
- Keys must be rotated periodically and never hardcoded

## Data Protection

- Never log PII, passwords, tokens, or full credit card numbers
- Mask sensitive data in logs and error messages
- API responses must not leak stack traces, internal IPs, or database errors

## Data Flow Tracing (Mandatory for Security Reviews)

When reviewing security-critical code, trace the complete data path:

1. **Client → Middleware**: Verify authentication choke points are correctly configured
2. **Middleware → API**: Verify authorization checks exist and are not bypassable
3. **API → Database/Admin SDK**: Verify privileged operations don't bypass security rules
4. **IDOR Prevention**: Every update/delete operation MUST verify resource ownership

## DevSecOps Checklist

- [ ] SAST/DAST integrated in CI/CD pipeline
- [ ] Dependency scanning enabled (Snyk, GitHub Security, Dependabot)
- [ ] Container images scanned before deployment
- [ ] Secrets managed via environment variables or vault (never hardcoded)
- [ ] Security headers configured (CSP, HSTS, X-Frame-Options, SameSite)
