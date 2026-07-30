# Security Checklist

Code security checklist. Referenced by the following roles:

| Role | Usage |
| --- | --- |
| **Devon** | Avoid patterns listed here while writing code (mastery not required; focus on proactively avoiding common patterns) |
| **Prism** | Shallow quick scan (identify obvious patterns, e.g. `eval()`/`exec()`/hardcoded secrets) |
| **Judge** | Deep audit baseline (S-tier, per-milestone; semantic-layer vulnerabilities + contextual reasoning) |

> **Update mechanism**: New vulnerability type → project owner adds an entry → triggers Judge re-audit.
> **Scope**: This checklist is the default baseline (applies to all projects); projects may append project-specific entries at the end.

---

## Input Validation

- [ ] All user input is validated (whitelist preferred over blacklist)
- [ ] SQL queries use parameterization (prevent SQL injection)
- [ ] HTML output is escaped (prevent XSS)
- [ ] File paths do not accept user input (prevent path traversal)
- [ ] URL/redirect targets use a whitelist (prevent open redirect)
- [ ] Command execution does not accept concatenated user input (prevent command injection)
- [ ] Deserialization uses trusted formats (prevent deserialization vulnerabilities)

## Authentication & Authorization

- [ ] Passwords are stored with bcrypt/argon2/scrypt (no plaintext, no MD5/SHA1)
- [ ] Session tokens are random and unpredictable (use the `secrets` module, not `random`)
- [ ] Every endpoint has an authorization check (not just at the UI layer)
- [ ] JWT/session has an expiration time (no long-lived or non-expiring tokens)
- [ ] API keys/passwords are not in code or logs
- [ ] Failed logins have a rate limit
- [ ] Password reset tokens are single-use and expire
- [ ] Multi-factor authentication is used for sensitive operations

## Data Protection

- [ ] Sensitive data (PII / financial / health) is encrypted at rest
- [ ] All external communication uses HTTPS
- [ ] Database connections use TLS (not plaintext)
- [ ] Backups are encrypted and stored offline
- [ ] Key management uses a dedicated service (AWS KMS / Vault / ...), not written into config files
- [ ] Logs do not print sensitive data (passwords, tokens, credit card numbers, SSNs)

## Error Handling

- [ ] Error messages do not leak internal details (stack traces, internal paths, library versions)
- [ ] Exceptions are logged but not exposed to end users
- [ ] Fail secure by default (fail closed): authorization check failure = deny, not allow
- [ ] Input validation failures do not return detailed errors (avoid attackers probing the system)

## Dependencies & Configuration

- [ ] Dependency versions are pinned (no ^/~, avoid auto-upgrades introducing vulnerabilities)
- [ ] Regularly audit dependencies for vulnerabilities (`npm audit` / `pip-audit` / `cargo audit`)
- [ ] Config files are not in git (`.gitignore` secrets)
- [ ] Production environment does not enable debug mode (no exposed tracebacks)
- [ ] CORS configuration is strict (no wildcard allowing any origin)
- [ ] CSP headers are set (if applicable)

## Logging & Monitoring

- [ ] Security events are auditable (login, permission changes, sensitive operations)
- [ ] Anomalous logins trigger alerts (different locations, frequent failures)
- [ ] Logs do not contain PII (unless necessary and encrypted)
- [ ] Logs have a retention policy (no permanent retention of sensitive data)

## Business Logic

- [ ] Fund/quantity operations are atomic (no partial commits)
- [ ] State machine transitions have legality checks (cannot jump from a terminal state back to the initial state)
- [ ] Critical operations have an idempotency key (prevent duplicate execution)
- [ ] Time window / timezone logic is explicit (avoid TOCTOU vulnerabilities)
- [ ] Sorting / priority logic does not leak internal information

## Framework / Language Specific

- Web: [ ] CSRF token, [ ] SameSite cookie, [ ] HttpOnly cookie
- SQL: [ ] parameterized (see Input Validation), [ ] ORM preferred
- Containers: [ ] non-root user, [ ] minimal base image, [ ] image scanning
- Kubernetes: [ ] RBAC, [ ] NetworkPolicy, [ ] Pod Security Standards
- AI/LLM: [ ] input sanitization (prevent prompt injection), [ ] output filtering (prevent information leakage), [ ] token limits

---

## Project-Specific Extensions

Append project-specific entries below (grouped by vulnerability category):

```markdown
### <Project Name> Specific

#### <Category 1>
- [ ] <Specific check>

#### <Category 2>
- [ ] <Specific check>
```

**Example**:

```markdown
### tracks Specific

#### Financial Transactions
- [ ] Order prices use integers (avoid floating-point precision issues)
- [ ] Fund operations have an idempotency key
- [ ] No overdraft allowed (account balance check must be inside the transaction)

#### Real-time Data
- [ ] Tick data has sequence numbers (prevent replay)
- [ ] Clock sync check (prevent time-series attacks)
```
