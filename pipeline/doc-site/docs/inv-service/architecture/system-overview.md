# System Overview — Architecture & Patterns

## Layered Architecture [Observed]

The application follows a classic three-tier layered architecture with cross-cutting concerns handled via filters:

```
┌─────────────────────────────────────────────────────────────────┐
│                     Controller Layer (REST API)                  │
│  @RestController classes handling HTTP requests                  │
│  • InventoryController, LocationController, AvailabilityController│
│  • SafetyStockController, EncodeController, PasswordController   │
└─────────────────────────────────────────────────────────────────┘
                               │
                               │ DTO requests/responses
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Service Layer (Business Logic)                │
│  @Service classes orchestrating domain operations                │
│  • InventoryService — main orchestration service                 │
│  • MovementService — stock movement recording                     │
│  • ReservationService — soft-hold/temporary reservation mgmt     │
│  • AvailabilityService — ATP calculations                         │
│  • SafetyStockService — policy enforcement                        │
│  • LocationService — location CRUD                               │
│  • KafkaProducerService — async messaging                          │
│  • Base64EncodingService — encoding utilities                     │
└─────────────────────────────────────────────────────────────────┘
                               │
                               │ Repository interfaces
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Repository Layer (Data Access)                 │
│  Spring Data JPA repositories extending JpaRepository            │
│  • InventoryStateRepository, MovementLedgerRepository            │
│  • ReservationRepository, LocationRepository                     │
│  • SafetyStockPolicyRepository, ReservedAggRepository            │
└─────────────────────────────────────────────────────────────────┘
```

**Cross-Cutting Concerns:**
- `@Component` Filters: `ApiLoggingFilter` (request logging), `ExceptionHandlingFilter` (serialization), `RequestCorrelationFilter` (MDC correlation IDs)
- `@ControllerAdvice`: `GlobalExceptionHandler` — centralized exception handling
- `@Component`: `TempReservationExpiryListener` — Redis message listener for expiration events

_(Tech stack: see current-state/inventory.md)_

---

## Top Hotspot Components [Observed]

Based on coupling analysis, here are the critical components:

| Component | Risk Profile | Why It Matters |
|-----------|--------------|----------------|
| `InventoryService` | High coupling | Central orchestration hub; depends on all other services; owns reservation lifecycle + movement coordination |
| `ReservationService` | High complexity | Multi-dependency (inventory state, reserved agg, safety stocks, Redis temp holds, Kafka events); implements soft-hold semantics |
| `KafkaProducerService` | Critical dependency | All async events flow through this service; movement recording, reservation state changes published here |
| `AvailabilityService` | Read-heavy hotspot | Calculates ATP by combining inventory state, reservations, and safety stock policies; frequently called for order planning |
| `MovementService` | Write-hotspot | Records all stock movements; writes to movement ledger, updates inventory state, publishes events |

**Note:** Coupling analysis shows no cross-package dependencies, indicating well-segregated concerns. High coupling exists within the service layer for good reason — these services share domain state.

---

## Data Flow [Inferred]

### Typical Request → Response Flow

```
1. HTTP Request (POST /inventory/movements)
   │
2. Filter Chain
   ├─ RequestCorrelationFilter (injects X-CORRELATION_ID to MDC)
   ├─ ApiLoggingFilter (logs request metadata)
   └─ ExceptionHandlingFilter (configures JSON serialization)
   │
3. Controller Method (InventoryController.recordMovement)
   ├─ Validates movement DTO
   └─ Delegates to MovementService
   │
4. Service Layer
   ├─ MovementService.validate()
   ├─ MovementService.recordMovement()
   │   ├─ Updates InventoryState (debit source, credit destination)
   │   ├─ Creates MovementLedger entry
   │   └─ Publishes MOVEMENT_RECORDED via KafkaProducerService
   │
5. Repository Layer
   ├─ InventoryStateRepository.save()
   └─ MovementLedgerRepository.save()
   │
6. Kafka Producer (async)
   └─ Sends event to MOVEMENT_RECORDED topic
   │
7. Response
   └─ 200 OK with updated InventoryState or MovementLedger
```

### Reservation Workflow (Complex Flow)

```
1. ReservationService.createReservation()
   ├─ Validates available quantity (calls AvailabilityService)
   ├─ Creates Reservation entity (status: SOFT_HELD)
   ├─ Updates ReservedAgg (increments qty_reserved by location/SKU)
   ├─ Creates Redis temp key for expiry tracking
   └─ Publishes RESERVATION_SOFT_HELD event via Kafka
   │
2. TempReservationExpiryListener (background)
   ├─ Listens on Redis key expiry events
   ├─ Triggers cancellation of expired soft-holds
   └─ Publishes RESERVATION_CANCELLED event

---
```

### Availability Check Flow

```
1. AvailabilityService.calculateATP(locationId, sku)
   ├─ Fetches InventoryState (onHand, damaged, inbound, quarantine)
   ├─ Fetches ReservedAgg (qtyReserved for location/SKU)
   ├─ Fetches SafetyStockPolicy (minimum required)
   └─ Computes: ATP = onHand + inbound - damaged - quarantine - qtyReserved - minQty
```

---

## External Systems [Observed]

| System | Purpose | Evidence |
|--------|---------|----------|
| **PostgreSQL** (Spring Data JPA) | Primary persistence for domain entities | `JpaRepository` interfaces on all repositories |
| **Redis** | Temp reservation expiry tracking, message listener | `StringRedisTemplate`, `RedisConnectionFactory`, `RedisMessageListenerContainer` |
| **Kafka** | Event streaming for async decoupling | `KafkaTemplate<String,String>`, six event topics defined |
| **HTTP/REST APIs** | External integrations | `@RestController` annotations, Jakarta servlet filters |

**Event Topics Published:**
- `MOVEMENT_RECORDED` — after stock movements
- `RESERVATION_SOFT_HELD` — on reservation creation
- `RESERVATION_CONFIRMED` — on reservation confirmation
- `RESERVATION_CANCELLED` — on cancellation/expiry
- `RESERVATION_RELEASED` — on reservation release
- `RESERVATION_SOFT_HOLD_RELEASED` — on temporary hold release
- `SAFETY_STOCK_POLICY_UPDATED` — on policy changes

_(Tech stack: see current-state/inventory.md)_

---

## Design Patterns [Observed]

### Pattern | Example | Evidence
|----------|---------|----------|
| **MVC/Clean Architecture** | Controllers → Services → Repositories | `@RestController` classes inject `@Service` beans which depend on `@Repository` interfaces |
| **Repository Pattern** | `InventoryStateRepository extends JpaRepository` | Spring Data JPA interfaces with method naming conventions |
| **Observer Pattern** | `TempReservationExpiryListener` | Implements Spring Redis message listener for key expiry events |
| **DTO Pattern** | Request/Response DTOs (e.g., `MovementRequest`, `InventoryStateResponse`) | Strict separation between API contracts and domain entities |
| **Template Method** | `OncePerRequestFilter.doFilterInternal()` | Base filter class overridden by custom filters |
| **Strategy (Implicit)** | `Base64EncodingService`, different encoding operations | Single service with configurable max bytes parameter |
| **Exception Handler** | `@ControllerAdvice GlobalExceptionHandler` | Centralized exception handling with `@ExceptionHandler` methods |

**No Design Smells Detected:** No circular dependencies between packages. Strong separation between controller, service, and repository layers.

---

## Architecture Summary

This is a well-structured Spring Boot inventory management service with:
- Clean separation of concerns (controller → service → repository)
- Event-driven architecture via Kafka for async operations
- Redis-backed soft-hold mechanism for reservation expiry
- Comprehensive filtering for monitoring and correlation
- No cross-package coupling — all dependencies remain within `com.inventory.inventoryservice`

Key tradeoff: Single monolithic `inventory-service` package contains all logic. No clear bounded context boundaries within the package, but strong internal cohesion.

_(Tech stack: see current-state/inventory.md)_