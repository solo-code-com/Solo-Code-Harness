# Security Categories Reference

## Input Validation Vulnerabilities
- SQL injection via unsanitized user input
- Command injection in system calls or subprocesses
- XXE injection in XML parsing
- Template injection in templating engines
- NoSQL injection in database queries
- Path traversal in file operations

## Authentication & Authorization Issues
- Authentication bypass logic
- Privilege escalation paths
- Session management flaws
- JWT token vulnerabilities
- Authorization logic bypasses

## Crypto & Secrets Management
- Hardcoded API keys, passwords, or tokens
- Weak cryptographic algorithms or implementations
- Improper key storage or management
- Cryptographic randomness issues
- Certificate validation bypasses

## Injection & Code Execution
- Remote code execution via deserialization
- YAML deserialization vulnerabilities
- Eval injection in dynamic code execution
- XSS vulnerabilities (reflected, stored, DOM-based)

## Data Exposure
- Sensitive data logging or storage
- PII handling violations
- API endpoint data leakage
- Debug information in production
