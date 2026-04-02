---
title: "E2E Scenarios — Create Reservation (Soft Hold)"
type: "e2e-test-scenario"
flow: "create-reservation"
entry_point: "com.inventory.inventoryservice.controller.InventoryController.createReservation(ReservationCreateRequest)"
evidence: "Observed"
timestamp: "2025-01-24T00:00:00Z"
---

# Flow: Create Reservation (Soft Hold)

## 3a. Flow Summary [Observed]

| Attribute | Value |
|---|---|
| **Entry Point** | `POST /inventory/reservations` (inferred path) |
| **Controller Method** | `InventoryController.createReservation(ReservationCreateRequest)` — line 81 |
| **Returns** | `ResponseEntity<String>` (likely a reservation ID or status message) |
| **Description** | Places a soft hold on inventory for an order. Checks available stock (onHand − reserved − safetyStock) against requested qty. Writes a Redis TTL key for expiry handling. Publishes a Kafka event. If the order already has a reservation in a compatible state, handles accordingly (IF branches at lines 74, 79, 89). |
| **Layers Traversed** | `InventoryController` → `ReservationService.createReservation(sku, locationId, qty, orderId)` → `ReservationRepository` + `InventoryStateRepository` + `ReservedAggRepository` + `SafetyStockPolicyRepository` → DB + Redis → `KafkaProducerService` |
| **Transaction** | `@Transactional` on `ReservationService.createReservation` |
| **Kafka Event** | `RESERVATION_SOFT_HELD_TOPIC` (via `sendReservationSoftHeldEvent`) |
| **Redis** | TTL key written per soft-held reservation |

### Call Chain (Observed)
```
InventoryController.createReservation(ReservationCreateRequest)
  └─ ReservationService.createReservation(sku, locationId, qty, orderId) [@Transactional]
       ├─ ReservationRepository.findByOrderId(orderId) (line 74)     ← IF: existing reservation?
       ├─ getStockComponentsFromDB(sku, locationId)                  ← IF: sufficient stock?
       │    ├─ InventoryStateRepository.findBySkuAndLocationId(sku, locationId)
       │    ├─ ReservedAggRepository.findBySkuAndLocationId(sku, locationId)
       │    └─ SafetyStockPolicyRepository.findActivePolicy(sku, locationId, now)
       ├─ IF line 89: stock check branching
       ├─ Reservation entity saved (line 83-98)
       ├─ ReservedAgg updated (line 93-98)
       ├─ Redis TTL key written (TEMP_RESERVATION_PREFIX + orderId)
       └─ KafkaProducerService.sendReservationSoftHeldEvent(sku, locationId, qty, orderId) (line 100)
```

---

## 3b. Test Data Setup

### Required DB State
- `InventoryState(sku="SKU-001", locationId=1)` with `onHand=100`
- `ReservedAgg(sku="SKU-001", locationId=1)` with `qtyReserved=20` (or none → 0)
- Optionally: active `SafetyStockPolicy(sku="SKU-001", locationId=1)` with `minQty=10`
- No existing `Reservation` for `orderId="ORD-001"` (first-time creation)

### Input Payload — `ReservationCreateRequest`
```json
{
  "sku": "SKU-001",
  "locationId": 1,
  "qty": 30,
  "orderId": "ORD-001"
}
```

### Field Types [Observed]
| Field | Type | Notes |
|---|---|---|
| `sku` | `String` | Required |
| `locationId` | `Long` | Required |
| `qty` | `Integer` | Required, positive |
| `orderId` | `String` | Required, unique per order |

### Available Stock Calculation [Inferred]
```
availableToPromise (ATP) = onHand - qtyReserved - safetyStock
```
Test data must ensure: `ATP >= qty requested`

---

## 3c. Happy Path Scenarios

### HP-1: New Soft Hold with Sufficient Stock
**Given**:
- `onHand=100`, `qtyReserved=20`, `safetyStock=10` → ATP = 70
- No existing reservation for `orderId="ORD-001"`
- Requesting `qty=30`

**When**: `POST /inventory/reservations`

**Then**:
- HTTP 200 OK (inferred; returns `ResponseEntity<String>`)
- `Reservation` entity created with `status=SOFT_HELD` [Inferred from Kafka event `RESERVATION_SOFT_HELD_TOPIC`]
- `ReservedAgg.qtyReserved` incremented by 30 (→ 50)
- Redis TTL key `TEMP_RESERVATION_PREFIX + "ORD-001"` created with configured TTL
- Kafka event published to `RESERVATION_SOFT_HELD_TOPIC`

### HP-2: Soft Hold with Exact ATP Stock
**Given**:
- `onHand=50`, `qtyReserved=10`, `safetyStock=10` → ATP = 30
- Requesting `qty=30` (exactly equal to ATP)

**When**: `POST /inventory/reservations`

**Then**:
- HTTP 200 OK
- Reservation created successfully
- `qtyReserved` → 40

### HP-3: Reservation with No Safety Stock Policy
**Given**:
- No active `SafetyStockPolicy` for this SKU × location
- `onHand=100`, `qtyReserved=0` → ATP = 100 (safetyStock treated as 0)
- Requesting `qty=50`

**When**: `POST /inventory/reservations`

**Then**:
- HTTP 200 OK
- Reservation created

### HP-4: Idempotent Re-request for Same Order (existing reservation in terminal state)
**Given**:
- `ReservationRepository.findByOrderId("ORD-002")` returns a reservation in `RELEASED` or `CANCELLED` status (IF branch at line 74/79)

**When**: `POST /inventory/reservations` for the same `orderId`

**Then**:
- [Hypothesized]: New soft hold created (old reservation in terminal state is ignored)
- HTTP 200 OK

---

## 3d. Error Path Scenarios

### EP-1: Insufficient Stock (ATP < requested qty)
**Given**:
- `onHand=50`, `qtyReserved=30`, `safetyStock=10` → ATP = 10
- Requesting `qty=30`

**When**: `POST /inventory/reservations`

**Then**:
- [Inferred from IF branch at line 89]: `IllegalArgumentException` thrown → HTTP 400 via `handleIllegalArgument`
- Body: `ErrorResponse { error, message: "Insufficient stock…", requestId }`
- No `Reservation` persisted, `ReservedAgg` unchanged, no Redis key, no Kafka event

### EP-2: Duplicate Order ID — Active Reservation Already Exists
**Given**:
- `ReservationRepository.findByOrderId("ORD-001")` returns a reservation with `status=SOFT_HELD` or `CONFIRMED`

**When**: `POST /inventory/reservations` for the same `orderId="ORD-001"`

**Then**:
- [Inferred from IF branch at line 74]: Exception or conflict response
- [Hypothesized]: `IllegalArgumentException("Reservation already exists for order …")` → HTTP 400

### EP-3: No InventoryState for SKU × Location
**Given**:
- No `InventoryState` row for the requested SKU and location

**When**: `POST /inventory/reservations`

**Then**:
- [Hypothesized]: `onHand` treated as 0 → ATP ≤ 0 → insufficient stock error → HTTP 400
- Or `NullPointerException` / `IllegalArgumentException` if empty Optional not guarded

### EP-4: Invalid Request — Missing Fields
**Given**: `ReservationCreateRequest` with `orderId=null`

**When**: `POST /inventory/reservations`

**Then**:
- HTTP 400 — `handleValidation` (if `@NotNull` present) or `handleIllegalArgument`

### EP-5: Redis Write Failure
**Given**: Redis is down when attempting to write TTL key

**When**: `POST /inventory/reservations`

**Then**:
- [Hypothesized]: Exception propagates → `@Transactional` rollback → HTTP 500 via `handleGeneral`
- No `Reservation` persisted, no Kafka event

---

## 3e. Edge Cases

### EC-1: ATP Exactly Zero
**Given**: `onHand=10`, `qtyReserved=0`, `safetyStock=10` → ATP = 0
**When**: `POST /inventory/reservations` with `qty=1`
**Then**: HTTP 400 — Insufficient stock

### EC-2: Safety Stock Policy Not Yet Active
**Given**: `SafetyStockPolicy.effectiveFrom` is in the future
**When**: `POST /inventory/reservations`
**Then**: Safety stock treated as 0 for this check (`findActivePolicy` returns empty) → ATP higher → possible over-allocation risk

### EC-3: Redis TTL Expiry Triggers Kafka Event
This is a downstream / async edge case:
1. Reservation soft-held, TTL key written in Redis
2. TTL expires → `TempReservationExpiryListener.onMessage` fires (IF branch at line 22 matches prefix)
3. Kafka `RESERVATION_SOFT_HOLD_RELEASED_TOPIC` event published [Inferred]
4. Assert: `qtyReserved` decremented, `Reservation.status` updated to `RELEASED` [Hypothesized — listener may need to update DB state]

### EC-4: Very Large `qty` Request
**Given**: `qty=Integer.MAX_VALUE`
**When**: `POST /inventory/reservations`
**Then**: `ReservedAgg.qtyReserved` overflow check; assert `@Max` validation or guard exists

### EC-5: Concurrent Reservations for Same SKU
**Given**: Two simultaneous reservation requests for `sku="SKU-001"` at same location, each requesting qty that individually fits but together would exceed ATP
**When**: Both requests fire simultaneously
**Then**: `@Transactional` serialization; one succeeds, one gets insufficient-stock error. Assert no double-booking.

---

## 3f. Mock Boundaries

| Dependency | Method | Happy Path Stub | Error Path Stub |
|---|---|---|---|
| `ReservationRepository` | `findByOrderId(String)` | Return `Optional.empty()` (new order) | Return existing SOFT_HELD reservation |
| `InventoryStateRepository` | `findBySkuAndLocationId(String, Long)` | Return state with `onHand=100` | Return `Optional.empty()` |
| `ReservedAggRepository` | `findBySkuAndLocationId(String, Long)` | Return agg with `qtyReserved=20` | Return empty (no prior reservations) |
| `SafetyStockPolicyRepository` | `findActivePolicy(String, Long, OffsetDateTime)` | Return policy with `minQty=10` | Return `Optional.empty()` |
| `ReservationRepository` | `save(Reservation)` | Return saved entity | Throw `DataIntegrityViolationException` |
| `ReservedAggRepository` | `save(ReservedAgg)` | Return updated agg | Throw `DataIntegrityViolationException` |
| `StringRedisTemplate` | `opsForValue().set(key, value, ttl)` | No-op | Throw `RedisConnectionFailureException` |
| `KafkaProducerService` | `sendReservationSoftHeldEvent(String, Long, Integer, String)` | No-op | Throw `KafkaException` |
