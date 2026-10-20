# Python Coding Rules

> Auto-loaded when editing `.py` files. Enforces PEP 8, Pythonic patterns, security.

## Style (PEP 8)

- 4 spaces indentation (no tabs)
- Max 88 characters per line (Black default)
- `snake_case` for functions, variables, modules
- `PascalCase` for classes, `UPPER_CASE` for constants
- Imports: stdlib → third-party → local, each group alphabetically

## Type Hints (Mandatory)

- All public functions MUST have type annotations
- Use `Optional[X]` not `X | None` for Python < 3.10
- Use `list[X]` / `dict[K, V]` (Python 3.9+ generic syntax)
- Never use `Any` unless truly dynamic — use `Protocol`, `TypeVar`, or concrete types

## Pythonic Patterns

```python
# GOOD
squares = [x**2 for x in range(10)]           # comprehension
if isinstance(obj, MyClass):                   # isinstance, not type()
first = items[0] if items else None            # ternary
with open('file') as f:                        # context manager
    data = f.read()

# BAD
squares = []
for x in range(10): squares.append(x**2)       # C-style loop
if type(obj) == MyClass:                       # type() comparison
first = items and items[0] or None             # confusing short-circuit
f = open('file'); data = f.read(); f.close()   # manual resource mgmt
```

## Security (CRITICAL)

- **SQL**: Always parameterized queries — NEVER f-strings
  ```python
  cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))  # GOOD
  cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")     # BAD
  ```
- **Secrets**: Use `os.environ.get()` — never hardcode
- **Path traversal**: `pathlib.Path().resolve()` + validate with `.is_relative_to()`
- **Serialization**: Never `pickle.load()` untrusted data
- **YAML**: Use `yaml.safe_load()` not `yaml.load()`
- **Subprocess**: Use `subprocess.run(cmd, shell=False)` with list args

## Error Handling

```python
# GOOD
try:
    result = risky_operation()
except ValueError as e:
    logger.error("Invalid value: %s", e)
    raise
except ConnectionError:
    logger.warning("Retrying...")
    return fallback()

# BAD
try:
    risky_operation()
except:          # bare except
    pass         # silent failure
```

## Testing

- Use `pytest` (not unittest.TestCase)
- Follow TDD: RED → GREEN → REFACTOR
- Target 80%+ coverage
- Test both happy path AND error paths
- Use `pytest.fixture` for shared setup
- Mock external dependencies (`unittest.mock` or `pytest-mock`)

## Project Structure

```
src/
├── __init__.py
├── models/       # Data models
├── services/     # Business logic
├── api/          # HTTP/routes
├── utils/        # Helper functions
└── config.py     # Configuration

tests/
├── conftest.py   # Shared fixtures
├── test_models/
├── test_services/
└── test_api/
```
