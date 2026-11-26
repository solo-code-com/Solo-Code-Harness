---
name: "rules-typescript"
description: "Auto-loaded when editing `.ts`, `.tsx`, `.js`, `.jsx` files."
---

# TypeScript/JavaScript Coding Rules

> Auto-loaded when editing `.ts`, `.tsx`, `.js`, `.jsx` files.

## Type Safety

- **No `any`** — use `unknown` + type guards, or proper types
- **Prefer type inference** — don't annotate obvious types
- **Discriminated unions** for state machines
- **`satisfies`** operator (TS 4.9+) for type-safe config objects
- **Never `@ts-ignore`** — fix the type or use `@ts-expect-error` with comment

## Code Style

- `const` by default, `let` only when reassigned (never `var`)
- Arrow functions for callbacks, `function` for top-level
- Template literals over string concatenation
- Optional chaining `?.` over nested `&&` checks
- Nullish coalescing `??` over `||` for default values
- Named exports preferred over default exports

## React Patterns

```tsx
// GOOD
const UserList = ({ users }: { users: User[] }) => (
  <ul>
    {users.map(user => <UserItem key={user.id} user={user} />)}
  </ul>
);

// BAD — index as key, inline handlers
{users.map((u, i) => <li key={i} onClick={() => delete(u.id)}>{u.name}</li>)}
```

- **Keys**: Use stable IDs, never array index
- **State**: Never mutate — use setState callback form
- **useEffect**: Always specify dependencies, cleanup subscriptions
- **Memo**: `useMemo` for expensive computations, `useCallback` for stable references
- **Avoid**: Props drilling > 3 levels — use context or composition

## Security (CRITICAL)

- **XSS**: Never use `dangerouslySetInnerHTML` without DOMPurify
- **Secrets**: No API keys in client bundle — use server-side routes
- **Input validation**: Validate ALL user input (Zod, Yup, joi)
- **Auth tokens**: Store in httpOnly cookies, not localStorage
- **URL params**: Sanitize before use in fetch/redirect

## Error Handling

```typescript
// GOOD
try {
  const data = await fetchUser(id);
  return data;
} catch (error) {
  logger.error('Failed to fetch user', { id, error });
  throw new AppError('User not found', 404);
}

// BAD
const data = await fetchUser(id); // unhandled rejection
```

- Always handle Promise rejections
- Use custom error classes with status codes
- Never expose internal errors to client

## Testing

- Use `vitest` or `jest`
- Target 80%+ coverage
- **Unit**: Test individual functions/utilities
- **Integration**: Test API routes with supertest
- **E2E**: Playwright for critical flows
- Mock external dependencies (fetch, DB, SDKs)

## Project Structure

```
src/
├── app/          # Next.js app router or routes
├── components/   # Reusable UI components
│   └── ui/       # Base UI primitives
├── lib/          # Utilities, API clients
├── hooks/        # Custom React hooks
├── types/        # Shared TypeScript types
└── server/       # Server-only code
```
