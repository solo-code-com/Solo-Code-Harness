---
name: codebase-design
description: "Shared vocabulary for designing deep modules. Use when: design a module interface, decide where a seam goes, make code more testable, reduce coupling, evaluate if a module is too shallow, or another skill needs the deep-module vocabulary."
license: MIT
---

# Codebase Design

Design **deep modules**: a lot of behaviour behind a small interface, placed at a clean seam, testable through that interface. The aim is **leverage** for callers, **locality** for maintainers, and **testability** for everyone.

Use this language and these principles wherever code is designed or restructured. Consistent vocabulary is the whole point — don't substitute "component," "service," "API," or "boundary."

---

## Glossary

**Module** — anything with an interface and an implementation. Scale-agnostic: a function, class, package, or tier-spanning slice.
*Avoid:* unit, component, service.

**Interface** — everything a caller must know to use the module correctly: type signature + invariants + ordering constraints + error modes + required configuration + performance characteristics.
*Avoid:* API, signature (both too narrow — they refer only to the type-level surface).

**Implementation** — what is inside a module, its body of code. Distinct from **Adapter**: a thing can be a small adapter with a large implementation (a Postgres repo) or a large adapter with a small implementation (an in-memory fake). Use "adapter" when the seam is the topic; "implementation" otherwise.

**Depth** — leverage at the interface: amount of behaviour a caller (or test) can exercise per unit of interface they must learn. A module is **deep** when a large amount of behaviour sits behind a small interface, **shallow** when the interface is nearly as complex as the implementation.

**Seam** *(Michael Feathers)* — a place where you can alter behaviour without editing in that place; the *location* at which a module's interface lives.
*Avoid:* boundary (overloaded with DDD's bounded context).

**Adapter** — a concrete thing that satisfies an interface at a seam. Describes *role* (what slot it fills), not substance (what is inside).

**Leverage** — what callers get from depth: more capability per unit of interface they learn. One implementation pays back across N call sites and M tests.

**Locality** — what maintainers get from depth: change, bugs, knowledge, and verification concentrate in one place rather than spreading across callers. Fix once, fixed everywhere.

---

## Deep vs shallow

**Deep module** = small interface + lots of implementation:
```
┌─────────────────────┐
│   Small Interface   │  ← Few methods, simple params
├─────────────────────┤
│                     │
│  Deep Implementation│  ← Complex logic hidden
│                     │
└─────────────────────┘
```

**Shallow module** = large interface + little implementation (avoid):
```
┌─────────────────────────────────┐
│       Large Interface           │  ← Many methods, complex params
├─────────────────────────────────┤
│  Thin Implementation            │  ← Just passes through
└─────────────────────────────────┘
```

When designing an interface, ask:
- Can I reduce the number of methods?
- Can I simplify the parameters?
- Can I hide more complexity inside?

---

## Principles

- **Depth is a property of the interface, not the implementation.** A deep module can be internally composed of small, mockable, swappable parts — they just are not part of the interface. A module can have **internal seams** (private to its implementation, used by its own tests) as well as the **external seam** at its interface.
- **The deletion test.** Imagine deleting the module. If complexity vanishes, it was a pass-through. If complexity reappears across N callers, it was earning its keep.
- **The interface is the test surface.** Callers and tests cross the same seam. If you want to test *past* the interface, the module is probably the wrong shape.
- **One adapter means a hypothetical seam. Two adapters means a real one.** Do not introduce a seam unless something actually varies across it.

---

## Designing for testability

### 1. Accept dependencies, do not create them

```typescript
// HARD TO TEST: creates its own dependency
function processOrder(order) {
  const gateway = new StripeGateway();     // cannot inject a fake
}

// TESTABLE: accepts dependency
function processOrder(order, paymentGateway: PaymentGateway) {
  // test by passing a mock gateway
}
```

```python
# HARD TO TEST: creates its own dependency
def send_notification(user):
    client = boto3.client("sns")          # cannot mock
    client.publish(...)

# TESTABLE: accepts dependency
def send_notification(user, sns_client):
    sns_client.publish(...)                # test with moto or MagicMock
```

### 2. Return results, do not produce side effects

```typescript
// HARD TO TEST: mutates input
function applyDiscount(cart): void {
  cart.total -= discount;                  # must inspect cart after call
}

// TESTABLE: returns result
function calculateDiscount(cart): Discount {
  // test by checking returned value
}
```

### 3. Small surface area

Fewer methods = fewer tests needed. Fewer params = simpler test setup. If a method has 5+ params, consider an options object or splitting the concern.

---

## Rejected framings

- **Depth as ratio of implementation-lines to interface-lines** (Ousterhout): rewards padding the implementation. Use depth-as-leverage instead.
- **"Interface" as the TypeScript `interface` keyword or a class's public methods**: too narrow — interface here includes every fact a caller must know.
- **"Boundary"**: overloaded with DDD's bounded context. Say **seam** or **interface**.

---

To explore alternative interface designs by comparing multiple approaches, use skill `subagent-driven-development`.
