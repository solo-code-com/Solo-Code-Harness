---
name: "rules-database"
description: "Auto-loaded when editing SQL, migrations, ORM models, or database config."
---

# Database Rules

> Auto-loaded when editing SQL, migrations, ORM models, or database config.

## Query Patterns

### Parameterized Queries (MANDATORY)

```python
# GOOD
cursor.execute("SELECT * FROM users WHERE email = ?", (email,))

# BAD — SQL Injection
cursor.execute(f"SELECT * FROM users WHERE email = '{email}'")
```

### Index Strategy

- Index ALL foreign keys
- Index columns in WHERE, JOIN, ORDER BY
- Composite index: equality columns first, then range
- Partial indexes for soft deletes: `WHERE deleted_at IS NULL`

### Pagination

```sql
-- GOOD: Cursor-based
SELECT * FROM orders WHERE id > $last_id ORDER BY id LIMIT 20;

-- BAD: Offset on large tables
SELECT * FROM orders ORDER BY id LIMIT 20 OFFSET 100000;
```

## Schema Design

- **IDs**: `bigint` (not `int`), use `IDENTITY` or UUIDv7
- **Timestamps**: `timestamptz` (not `timestamp`)
- **Money**: `numeric(19,4)` or `bigint` (cents)
- **Strings**: `text` (not `varchar(255)` unless constraint needed)
- **Booleans**: `boolean` (not `integer`)
- **Naming**: `lowercase_snake_case` — no quoted mixed-case

## Migration Rules

- Always create reversible migrations (up + down)
- Never modify existing migrations — create new ones
- Test migrations on staging before production
- Include data migrations alongside schema changes
- Run `EXPLAIN` on new queries before deploying

## Security

- Parameterized queries — NEVER string concatenation
- Least privilege: application user needs SELECT/INSERT/UPDATE/DELETE only
- Row Level Security (RLS) for multi-tenant tables
- Encrypt sensitive columns at rest (PII, credentials)
- Validate input lengths before DB insert

## Performance

- Avoid N+1 queries — use JOINs, batch queries, eager loading
- Keep transactions SHORT — no API calls inside transactions
- Use connection pooling (PgBouncer for PostgreSQL)
- Monitor slow queries with `pg_stat_statements`
- `EXPLAIN ANALYZE` before deploying complex queries
