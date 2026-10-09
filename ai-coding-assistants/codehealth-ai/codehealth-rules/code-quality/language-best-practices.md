# Language Best Practices Reference

## Purpose
Comprehensive best practices catalog per language. Used during Phase 4 (Code Quality)
to assess adherence beyond anti-patterns — covering style, performance, security,
and idiomatic patterns.

---

## TypeScript / JavaScript Best Practices

### Type Safety
| Practice | Good | Bad | Severity |
|----------|------|-----|----------|
| Strict mode | `"strict": true` in tsconfig | No strict | 🟡 |
| Avoid `any` | Use `unknown` + type guards | `any` everywhere | 🟡 |
| Discriminated unions | `type Result = Success | Error` | String type checks | 🔵 |
| Readonly by default | `readonly` on properties | Mutable when not needed | 🔵 |
| Exhaustive checks | `never` in switch default | Missing cases silently ignored | 🟡 |
| Zod/io-ts at boundaries | Validate external data at entry | Trust external input shape | 🟠 |

### Async Patterns
| Practice | Good | Bad | Severity |
|----------|------|-----|----------|
| Promise.all for independent | Parallel execution | Sequential awaits | 🟡 |
| Error handling | try/catch or .catch() on every async | Unhandled rejections | 🟠 |
| AbortController | Cancel abandoned requests | Fetch without timeout/cancel | 🟡 |
| Avoid mixing callbacks + promises | Pure async/await | Callback inside async function | 🟡 |
| Stream for large data | createReadStream/pipeline | readFileSync/readFile for large files | 🟠 |

### Performance
| Practice | Good | Bad | Severity |
|----------|------|-----|----------|
| Use Map/Set for lookups | O(1) access | Array.find/includes in loops O(n) | 🟡 |
| Avoid spread in loops | Pre-allocate or push | `[...acc, item]` in reduce | 🟡 |
| Web Workers for CPU tasks | Off main thread | Heavy computation blocking UI | 🟠 |
| Debounce/throttle events | Controlled frequency | Handler on every keystroke/scroll | 🟡 |
| Lazy imports | Dynamic `import()` for routes | Import everything at startup | 🟡 |

### Security
| Practice | Good | Bad | Severity |
|----------|------|-----|----------|
| Parameterized queries | Prisma/ORM bindings | Template literal SQL | 🔴 |
| Input validation | Zod/Joi at API boundary | Trust req.body shape | 🟠 |
| No eval/Function | Static code only | eval(userInput) | 🔴 |
| CSP headers | Strict Content-Security-Policy | No CSP | 🟡 |
| HttpOnly cookies | `httpOnly: true` for session | Accessible to JS | 🟠 |
| Sanitize HTML output | DOMPurify or escape | Raw innerHTML from user data | 🔴 |

### Node.js Specific
| Practice | Good | Bad | Severity |
|----------|------|-----|----------|
| Graceful shutdown | Handle SIGTERM, drain connections | Process.exit() immediately | 🟠 |
| Cluster/PM2 for production | Multi-process utilization | Single process on multi-core | 🟡 |
| Structured logging | JSON logs with context | console.log() in production | 🟡 |
| Environment config validation | Fail fast on missing vars | Silent undefined values | 🟡 |
| Helmet for Express | Security headers middleware | No security headers | 🟡 |

---

## Python Best Practices

### Type Safety & Style
| Practice | Good | Bad | Severity |
|----------|------|-----|----------|
| Type hints | Full annotations + mypy | No type hints | 🟡 |
| Dataclasses/Pydantic | Structured data models | Plain dicts everywhere | 🟡 |
| f-strings | `f"Hello {name}"` | `"Hello " + name` or `.format()` | 🔵 |
| Walrus operator (3.8+) | `if (n := len(a)) > 10:` | Compute twice | 🔵 |
| match/case (3.10+) | Structural pattern matching | Long if/elif chains | 🔵 |
| `__slots__` on hot classes | Memory efficiency | Default `__dict__` for many instances | 🔵 |

### Async & Concurrency
| Practice | Good | Bad | Severity |
|----------|------|-----|----------|
| asyncio.gather for parallel | Concurrent I/O | Sequential awaits | 🟡 |
| async context managers | `async with` for resources | Manual open/close | 🟡 |
| ThreadPoolExecutor for blocking | Don't block event loop | Sync I/O in async function | 🔴 |
| Avoid GIL-bound parallelism | multiprocessing for CPU | Threading for CPU work | 🟡 |
| Use connection pools | aiopg, databases, etc. | New connection per query | 🟠 |

### Performance
| Practice | Good | Bad | Severity |
|----------|------|-----|----------|
| Generator expressions | `sum(x for x in items)` | `sum([x for x in items])` (allocs list) | 🔵 |
| collections module | Counter, defaultdict, deque | Manual implementations | 🔵 |
| `__slots__` for data classes | 30-40% less memory per instance | Only for hot path classes | 🔵 |
| lru_cache for pure functions | `@functools.lru_cache` | Re-computing expensive results | 🟡 |
| Avoid global lookups in loops | Local variable reference | `global_dict[key]` 1M times | 🔵 |
| Use built-in functions | `map()`, `filter()`, `any()`, `all()` | Manual loops for simple transforms | 🔵 |
| Bulk DB operations | `bulk_create()`, `bulk_update()` | Save in loop | 🟠 |

### Security
| Practice | Good | Bad | Severity |
|----------|------|-----|----------|
| Parameterized queries | `cursor.execute(sql, params)` | f-string in SQL | 🔴 |
| secrets module for tokens | `secrets.token_urlsafe()` | `random.random()` for auth | 🔴 |
| No pickle from untrusted | JSON/MessagePack | `pickle.loads(user_data)` | 🔴 |
| No exec/eval | Static code only | `eval(user_input)` | 🔴 |
| hashlib for passwords | bcrypt/argon2/scrypt | md5/sha1 for passwords | 🔴 |
| SSRF prevention | Allowlist URLs | `requests.get(user_url)` | 🟠 |

### Django Specific
| Practice | Good | Bad | Severity |
|----------|------|-----|----------|
| select_related/prefetch_related | Eager loading | Lazy N+1 | 🟠 |
| QuerySet is lazy | Chain filters, evaluate once | `.all()` then Python filtering | 🟡 |
| Use F/Q objects | DB-side computation | Python-side computation on queryset | 🟡 |
| Migrations are forward-only | Never edit applied migrations | Modifying past migrations | 🟠 |
| Custom managers for reuse | `objects.active()` | Repeat `.filter(active=True)` | 🔵 |

---

## Java Best Practices

### Type Safety & Design
| Practice | Good | Bad | Severity |
|----------|------|-----|----------|
| Records for DTOs (16+) | `record Point(int x, int y)` | Boilerplate POJO | 🔵 |
| Sealed classes (17+) | Exhaustive type hierarchies | Open class hierarchies | 🔵 |
| Optional over null returns | `Optional<User>` | Return null | 🟡 |
| var for local variables | `var list = new ArrayList<>()` | Overly verbose types | 🔵 |
| Builder pattern | Fluent construction | 10-param constructors | 🟡 |
| Immutable collections | `List.of()`, `Map.of()` | Mutable collections leaked | 🟡 |

### Performance
| Practice | Good | Bad | Severity |
|----------|------|-----|----------|
| StringBuilder in loops | Single allocation | String += in loop | 🟡 |
| Stream API for collections | Lazy evaluation, parallel | Manual iterator loops for transforms | 🔵 |
| Connection pooling | HikariCP | New connection per query | 🔴 |
| Avoid autoboxing in loops | `int` not `Integer` in hot path | Boxing/unboxing millions of times | 🟡 |
| Virtual threads (21+) | `Thread.ofVirtual()` for I/O | Platform thread per request | 🟡 |
| JIT-friendly patterns | Final fields, small methods | Megamorphic call sites | 🔵 |

### Spring Boot Specific
| Practice | Good | Bad | Severity |
|----------|------|-----|----------|
| Constructor injection | `@RequiredArgsConstructor` | Field injection `@Autowired` | 🟡 |
| @Transactional scope | Minimal boundary | Entire service class @Transactional | 🟡 |
| DTO projection | Return DTOs from repository | Return entities to controller | 🟡 |
| @Cacheable | For expensive reads | No caching on repeat computations | 🟡 |
| Actuator health checks | `/actuator/health` configured | No observability | 🟡 |
| Profile-based config | `application-{profile}.yml` | Hardcoded environment values | 🟡 |

### Security
| Practice | Good | Bad | Severity |
|----------|------|-----|----------|
| Prepared statements | `PreparedStatement` | String concatenation in SQL | 🔴 |
| Spring Security defaults | Enable CSRF, CORS, headers | Disable without reason | 🟠 |
| Input validation | `@Valid` + Bean Validation | Trust request body | 🟠 |
| No deserialization of untrusted | Jackson with type filtering | ObjectInputStream from user | 🔴 |
| Secrets externalized | Spring Cloud Config / Vault | Properties file with passwords | 🟠 |

---

## Go Best Practices

### Idiomatic Go
| Practice | Good | Bad | Severity |
|----------|------|-----|----------|
| Error wrapping | `fmt.Errorf("context: %w", err)` | Return bare error without context | 🟡 |
| Error checking | `if err != nil { return err }` | `_, _ = function()` ignoring errors | 🟠 |
| Table-driven tests | `[]struct{ name, input, want }` | Individual test functions | 🔵 |
| Context propagation | Pass ctx to all I/O functions | No cancellation support | 🟠 |
| Interface segregation | Small interfaces (1-3 methods) | Large interfaces (>5 methods) | 🟡 |
| Accept interfaces, return structs | Flexible consumers | Return interfaces (hides info) | 🔵 |

### Concurrency
| Practice | Good | Bad | Severity |
|----------|------|-----|----------|
| Context for cancellation | `ctx, cancel := context.WithTimeout(...)` | Goroutine without cancel | 🟠 |
| errgroup for parallel | `golang.org/x/sync/errgroup` | Manual goroutine + channel sync | 🟡 |
| Buffered channels when known | `make(chan T, n)` if producer count known | Unbuffered causing deadlock | 🟠 |
| sync.Once for initialization | Thread-safe lazy init | Race condition on first use | 🟠 |
| sync.Pool for hot allocations | Reduce GC pressure | Allocate in hot loop | 🟡 |
| Limit goroutines | Semaphore pattern | Unbounded goroutine spawn | 🔴 |

### Performance
| Practice | Good | Bad | Severity |
|----------|------|-----|----------|
| strings.Builder | Single allocation for concat | `+` in loop | 🟡 |
| Pre-allocate slices | `make([]T, 0, expectedCap)` | Grow via append from 0 | 🔵 |
| Avoid interface{} in hot path | Concrete types | Type assertions in loop | 🔵 |
| Struct field ordering | Align fields to reduce padding | Random field order (struct bloat) | 🔵 |
| Use sync.Map for concurrent read-heavy | Optimized for many reads | `map` + `sync.Mutex` for read-heavy | 🔵 |

### Security
| Practice | Good | Bad | Severity |
|----------|------|-----|----------|
| Parameterized queries | `db.Query(sql, args...)` | `fmt.Sprintf` in SQL | 🔴 |
| crypto/rand for secrets | `crypto/rand.Read()` | `math/rand` for tokens | 🔴 |
| TLS verification on | Default `http.Client` | `InsecureSkipVerify: true` | 🟠 |
| Input length limits | `io.LimitReader` | Read unlimited user input | 🟠 |
| No os/exec with user input | Avoid shell execution | `exec.Command("sh", "-c", userInput)` | 🔴 |

---

## Rust Best Practices

### Ownership & Safety
| Practice | Good | Bad | Severity |
|----------|------|-----|----------|
| Prefer borrowing | `&str` over `String` in params | Clone everything | 🟡 |
| Use iterators | `.iter().map().collect()` | Manual indexing loops | 🔵 |
| Result over panic | `Result<T, E>` for fallible ops | `unwrap()` in library code | 🟠 |
| Avoid `.clone()` in hot path | Borrow or Arc | Clone large structs repeatedly | 🟡 |
| Cow for optional ownership | `Cow<'_, str>` | Always allocate String | 🔵 |

### Performance
| Practice | Good | Bad | Severity |
|----------|------|-----|----------|
| `Vec::with_capacity` | Pre-allocate known size | Push without capacity hint | 🔵 |
| `SmallVec` for small collections | Stack allocation for small N | Heap alloc for 1-2 items | 🔵 |
| Rayon for data parallelism | `par_iter()` for CPU work | Single-threaded large data | 🟡 |
| Avoid allocations in loops | Reuse buffers | `String::new()` per iteration | 🟡 |
| Profile before optimizing | `cargo flamegraph` | Premature optimization | 🔵 |

---

## Ruby Best Practices

### Performance
| Practice | Good | Bad | Severity |
|----------|------|-----|----------|
| Eager loading | `includes(:association)` | N+1 lazy loading | 🟠 |
| Pluck for single columns | `User.pluck(:email)` | `User.all.map(&:email)` | 🟡 |
| find_each for large sets | Batch processing (1000 at a time) | `.all.each` loading everything | 🟠 |
| Avoid method_missing | Explicit methods | Dynamic dispatch overhead | 🟡 |
| Freeze string literals | `# frozen_string_literal: true` | Mutable string allocation | 🔵 |
| Background jobs | Sidekiq/GoodJob for heavy work | Inline processing in request | 🟠 |

### Security
| Practice | Good | Bad | Severity |
|----------|------|-----|----------|
| Strong parameters | `params.require(:user).permit(:name)` | `params.to_unsafe_h` | 🔴 |
| CSRF protection | `protect_from_forgery` (default) | Skip CSRF without reason | 🟠 |
| SQL sanitization | `where(name: params[:name])` | `where("name = '#{params[:name]}'")` | 🔴 |
| Mass assignment | Explicit `attr_accessible` / strong params | Open assignment | 🔴 |
| No YAML.load from untrusted | `YAML.safe_load` | `YAML.load(user_input)` (RCE) | 🔴 |

---

## PHP Best Practices

### Modern PHP (8.x)
| Practice | Good | Bad | Severity |
|----------|------|-----|----------|
| Typed properties | `private string $name;` | No type declarations | 🟡 |
| Named arguments | `new User(name: 'Alice', email: ...)` | Positional with many params | 🔵 |
| Match expression | `match($status) { ... }` | Long switch with breaks | 🔵 |
| Enums (8.1+) | `enum Status: string { ... }` | String constants | 🟡 |
| Readonly properties (8.1+) | `public readonly string $id` | Manual immutability | 🔵 |
| Fibers (8.1+) | Async I/O without callbacks | Blocking everything | 🟡 |

### Security
| Practice | Good | Bad | Severity |
|----------|------|-----|----------|
| Prepared statements | PDO with `prepare()` + `execute()` | String interpolation in SQL | 🔴 |
| password_hash | `password_hash($pw, PASSWORD_BCRYPT)` | md5/sha1 for passwords | 🔴 |
| htmlspecialchars output | `htmlspecialchars($data, ENT_QUOTES)` | Echo raw user input | 🔴 |
| CSRF tokens | Verify on state-changing requests | No CSRF protection | 🟠 |
| No eval/system with user input | Static execution only | `eval($userInput)` | 🔴 |
| File upload validation | Check MIME type + extension + size | Trust file extension | 🟠 |

---

## Cross-Language Security Best Practices (Universal)

| # | Practice | Applies To | Severity |
|---|----------|-----------|----------|
| 1 | **Never trust user input** — validate and sanitize at every boundary | All | 🔴 |
| 2 | **Parameterize all queries** — never concatenate user data into SQL/commands | All | 🔴 |
| 3 | **Use constant-time comparison** for secrets/tokens | All | 🟠 |
| 4 | **Hash passwords with bcrypt/argon2/scrypt** — never MD5/SHA | All | 🔴 |
| 5 | **Use HTTPS everywhere** — no HTTP in production | All | 🟠 |
| 6 | **Principle of least privilege** — minimal permissions for service accounts | All | 🟠 |
| 7 | **Rotate secrets regularly** — and never hardcode them | All | 🟠 |
| 8 | **Log security events** — auth failures, permission denials, anomalies | All | 🟡 |
| 9 | **Set security headers** — CSP, X-Frame-Options, HSTS | Web apps | 🟡 |
| 10 | **Rate limit sensitive endpoints** — login, signup, password reset | APIs | 🟡 |

---

## How This File Is Used

During Phase 4 (Code Quality), this file is referenced to:
1. Score adherence to language best practices (percentage compliance)
2. Flag violations by severity
3. Prioritize fixes based on security > performance > style
4. Provide idiomatic "good" examples in recommendations

The analysis adapts to the DETECTED language — only relevant sections are applied.
