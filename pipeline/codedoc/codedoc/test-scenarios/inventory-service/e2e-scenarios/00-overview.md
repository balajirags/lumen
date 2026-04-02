---
title: "E2E Test Scenario Overview — Inventory Service"
type: "e2e-test-scenario"
flow: "overview"
entry_point: null
evidence: "Observed"
timestamp: "2025-01-24T00:00:00Z"
---

# Inventory Service — E2E Test Scenario Overview

## System Under Test

**Repository**: `inventory-service`
**Language / Framework**: Java 17 · Spring Boot · Spring Data JPA · Apache Kafka · Redis
**Architecture**: Layered REST service (Controller → Service → Repository → DB)

### Technology Stack [Observed]
| Layer | Technologies |
|-------|-------------|
| API | Spring `@RestController`, OpenAPI (`@Operation`, `@ApiResponse`) |
| Business Logic | `@Service` classes with `@Transactional` methods |
| Persistence | Spring Data JPA repositories (`JpaRepository`) |
| Messaging | Apache Kafka via `KafkaTemplate` (7 distinct event topics) |
| Caching / TTL | Redis (`StringRedisTemplate`, `TempReservationExpiryListener`) |
| Cross-Cutting | Servlet filters: `RequestCorrelationFilter`, `ExceptionHandlingFilter`, `ApiLoggingFilter` |
| Error Handling | `GlobalExceptionHandler` (`@RestControllerAdvice`) with 6 handler methods |

---

## Controllers and Their Base Paths [Observed]

| Controller | Base Path (inferred) | Methods |
|---|---|---|
| `InventoryController` | `/inventory` (inferred) | 13 endpoints |
| `AvailabilityController` | `/availability` (inferred) | 2 endpoints |
| `SafetyStockController` | `/safety-stock` (inferred) | 3 endpoints |
| `LocationController` | `/locations` (inferred) | 3 endpoints |
| `EncodeController` | `/encode` (inferred) | 1 endpoint |
| `PasswordController` | `/password` (inferred) | 1 endpoint |

---

## Domain Model Summary [Observed]

| Entity | Key Fields | Purpose |
|---|---|---|
| `InventoryState` | sku, locationId, onHand, damaged, quarantine, inbound | Tracks stock counts per SKU × location |
| `Reservation` | id (UUID), orderId, sku, locationId, qty, status | Holds stock for an order (lifecycle: SOFT_HELD → CONFIRMED / RELEASED / CANCELLED) |
| `ReservedAgg` | sku, locationId, qtyReserved | Aggregate reserved quantities for fast availability calculation |
| `MovementLedger` | sku, locationId, qty, type, referenceId, source | Immutable log of every stock movement |
| `SafetyStockPolicy` | sku, locationId, minQty, ruleType, effectiveFrom, effectiveTo | Time-bounded safety-stock rules |
| `Location` | id, name, locationType, isActive, addressLine1…countryCode | Physical warehouse/store locations |

### Enumerations [Observed]
- `MovementLedger.MovementType` — controls how stock counters change (SWITCH statement observed at line 56 of `MovementService.recordMovement`)
- `Reservation.ReservationStatus` — SOFT_HELD, CONFIRMED, RELEASED, CANCELLED (inferred from lifecycle methods)
- `Location.LocationType` — warehouse/store/etc (exact values [Unknown])

---

## Kafka Topics (Observed) [Observed]

| Topic Field | Event |
|---|---|
| `MOVEMENT_RECORDED_TOPIC` | Stock movement persisted |
| `RESERVATION_SOFT_HELD_TOPIC` | Reservation created (soft hold) |
| `RESERVATION_SOFT_HOLD_RELEASED_TOPIC` | Temp reservation expired (Redis TTL) |
| `RESERVATION_CONFIRMED_TOPIC` | Reservation confirmed |
| `RESERVATION_RELEASED_TOPIC` | Reservation released |
| `RESERVATION_CANCELLED_TOPIC` | Reservation cancelled |
| `SAFETY_STOCK_POLICY_UPDATED_TOPIC` | Safety stock policy created or updated |

---

## Redis Integration [Observed]

- `ReservationService` writes a TTL key per soft-held reservation with prefix `TEMP_RESERVATION_PREFIX` and TTL of `TEMP_RESERVATION_TTL_MINUTES`
- `TempReservationExpiryListener` listens for Redis key-expiry events; if the key matches the prefix, it triggers a `RESERVATION_SOFT_HOLD_RELEASED` event

---

## Selected Flows for E2E Test Coverage

The following **10 flows** were selected, prioritised by business criticality and complexity:

| # | Flow | Entry Point | File |
|---|---|---|---|
| 1 | Record Inventory Movement | `InventoryController.recordMovement` | `01-record-movement.md` |
| 2 | Transfer Inventory Between Locations | `InventoryController.transferInventory` | `02-transfer-inventory.md` |
| 3 | Create Reservation (Soft Hold) | `InventoryController.createReservation` | `03-create-reservation.md` |
| 4 | Confirm Reservation | `InventoryController.confirmReservation` | `04-confirm-reservation.md` |
| 5 | Release / Cancel Reservation | `InventoryController.releaseReservation` + `cancelReservation` | `05-release-cancel-reservation.md` |
| 6 | Ship Order | `InventoryController.ship` | `06-ship-order.md` |
| 7 | Check Inventory Availability | `AvailabilityController.getAvailability` + `getBulkAvailability` | `07-availability.md` |
| 8 | Manage Safety Stock Policy | `SafetyStockController` (create + update + get) | `08-safety-stock-policy.md` |
| 9 | Location Management | `LocationController` (create + update + list) | `09-location-management.md` |
| 10 | Query Inventory State & Snapshots | `InventoryController.getInventoryState` + `getReconciliationSnapshot` | `10-inventory-queries.md` |

---

## Cross-Cutting Test Concerns [Observed]

### Global Exception Handler
All flows must assert these error envelopes:

| Exception | HTTP Status | Handler Method |
|---|---|---|
| `MethodArgumentNotValidException` | 400 | `handleValidation` |
| `HttpMessageNotReadableException` | 400 | `handleMalformedJson` |
| `HttpMediaTypeNotSupportedException` | 415 | `handleUnsupportedMediaType` |
| `PayloadTooLargeException` | 413 | `handlePayloadTooLarge` |
| `IllegalArgumentException` | 400 | `handleIllegalArgument` |
| `Exception` (catch-all) | 500 | `handleGeneral` |

Error response body type: `ErrorResponse` (fields: `error`, `message`, `requestId`)

### Request Correlation Filter [Observed]
- Reads/generates `X-Request-ID` style header (MDC key observed)
- All error responses include `requestId` field in `ErrorResponse`

### Payload Size Filter [Observed]
- `PayloadTooLargeException` is thrown when request body exceeds configured max bytes
- Relevant for all POST/PUT endpoints

### API Logging Filter [Observed]
- Every request/response is logged; no test assertions required but note side effects for log-based testing

---

## Test Infrastructure Recommendations [Inferred]

- **Test framework**: JUnit 5 + Spring Boot Test (`@SpringBootTest`) + MockMvc
- **DB**: `@DataJpaTest` with embedded H2, or Testcontainers PostgreSQL for integration
- **Kafka**: Testcontainers Kafka, or `EmbeddedKafkaBroker`
- **Redis**: Testcontainers Redis, or `@EmbeddedRedis` (ozimov)
- **Mocks**: `@MockBean` for `KafkaProducerService` in controller/service integration tests
- **Auth**: No auth annotations observed — assume open endpoints [Hypothesized: may be behind gateway auth]
