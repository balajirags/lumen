---
title: "E2E Scenarios — Release and Cancel Reservation"
type: "e2e-test-scenario"
flow: "release-cancel-reservation"
entry_point: "com.inventory.inventoryservice.controller.InventoryController.releaseReservation(ReservationReleaseRequest) | cancelReservation(ReservationCancelRequest)"
evidence: "Observed"
timestamp: "2025-01-24T00:00:00Z"
---

# Flow: Release and Cancel Reservation

These two flows are documented together because they share identical structural patterns — both look up a reservation by `orderId`, validate its current status (IF at lines 183/209), update `ReservedAgg`, set the new status, and publish a Kafka event.

---

## 3a. Flow Summary [Observed]

### Release Reservation
| Attribute | Value |
|---|---|
| **Entry Point** | `POST /inventory/reservations/release` (inferred) |
| **Controller Method** | `InventoryController.releaseReservation(ReservationReleaseRequest)` — line 95 |
| **Returns** | `ResponseEntity<Reservation>` |
| **Description** | Voluntarily releases a soft-held or confirmed reservation back to available stock. Stock is returned to the pool. Typically used when a customer cancels before shipping. |
| **Transaction** | `@Transactional` on `ReservationService.releaseReservation` |
| **Kafka Event** | `RESERVATION_RELEASED_TOPIC` |

### Cancel Reservation
| Attribute | Value |
|---|---|
| **Entry Point** | `POST /inventory/reservations/cancel` (inferred) |
| **Controller Method** | `InventoryController.cancelReservation(ReservationCancelRequest)` — line 102 |
| **Returns** | `ResponseEntity<Reservation>` |
| **Description** | Cancels a reservation (typically a soft hold that was never confirmed). Returns stock to pool. Semantically distinct from release — represents an administrative or system-driven cancellation. |
| **Transaction** | `@Transactional` on `ReservationService.cancelReservation` |
| **Kafka Event** | `RESERVATION_CANCELLED_TOPIC` |

### Shared Call Chain (Observed)
```
releaseReservation(orderId) / cancelReservation(orderId)
  └─ ReservationService.releaseReservation(orderId) / cancelReservation(orderId) [@Transactional]
       ├─ ReservationRepository.findByOrderId(orderId)                (lines 180/206)
       ├─ IF: status check — guard invalid transitions              (lines 183/209)
       │    [e.g., already RELEASED/CANCELLED → throw exception]
       ├─ Reservation.setStatus(RELEASED / CANCELLED)               (lines 187/213)
       ├─ ReservedAggRepository.findBySkuAndLocationId(sku, locationId) (lines 192/218)
       ├─ ReservedAgg.setQtyReserved(current - reservation.qty)      (lines 194/220)
       ├─ ReservedAgg.setUpdatedAt(now)                              (lines 195/221)
       ├─ ReservationRepository.save / ReservedAggRepository.save    (inferred)
       └─ KafkaProducerService.sendReservationReleasedEvent / sendReservationCancelledEvent (lines 199/225)
```

---

## 3b. Test Data Setup

### Release — Required DB State
- `Reservation` with `orderId="ORD-001"`, `status=SOFT_HELD` or `CONFIRMED`, `qty=30`, `sku="SKU-001"`, `locationId=1`
- `ReservedAgg(sku="SKU-001", locationId=1)` with `qtyReserved=30`

### Release Input Payload — `ReservationReleaseRequest`
```json
{
  "orderId": "ORD-001"
}
```

### Cancel — Required DB State
- `Reservation` with `orderId="ORD-002"`, `status=SOFT_HELD`, `qty=20`, `sku="SKU-002"`, `locationId=2`
- `ReservedAgg(sku="SKU-002", locationId=2)` with `qtyReserved=20`

### Cancel Input Payload — `ReservationCancelRequest`
```json
{
  "orderId": "ORD-002"
}
```

---

## 3c. Happy Path Scenarios

### HP-1 (Release): Release a SOFT_HELD Reservation
**Given**: `Reservation(orderId="ORD-001", status=SOFT_HELD, qty=30, sku="SKU-001", locationId=1)`

**When**: `POST /inventory/reservations/release` with `{ "orderId": "ORD-001" }`

**Then**:
- HTTP 200 OK
- Response body: `Reservation` with `status=RELEASED`
- `Reservation.status` = `RELEASED` in DB
- `ReservedAgg.qtyReserved` decremented by 30 (e.g., 30 → 0)
- `ReservedAgg.updatedAt` refreshed
- Kafka event published to `RESERVATION_RELEASED_TOPIC`
- Redis TTL key removed or allowed to expire naturally [Hypothesized]

### HP-2 (Release): Release a CONFIRMED Reservation
**Given**: `Reservation(orderId="ORD-003", status=CONFIRMED, qty=15)`

**When**: `POST /inventory/reservations/release`

**Then**:
- HTTP 200 OK
- `Reservation.status` = `RELEASED`
- `ReservedAgg.qtyReserved` decremented by 15
- Kafka event published

### HP-3 (Cancel): Cancel a SOFT_HELD Reservation
**Given**: `Reservation(orderId="ORD-002", status=SOFT_HELD, qty=20)`

**When**: `POST /inventory/reservations/cancel`

**Then**:
- HTTP 200 OK
- Response body: `Reservation` with `status=CANCELLED`
- `ReservedAgg.qtyReserved` decremented by 20
- Kafka event published to `RESERVATION_CANCELLED_TOPIC`

### HP-4 (Cancel): Cancel a CONFIRMED Reservation
**Given**: `Reservation(orderId="ORD-004", status=CONFIRMED, qty=10)`

**When**: `POST /inventory/reservations/cancel`

**Then**:
- HTTP 200 OK
- `Reservation.status` = `CANCELLED`
- Stock returned to available pool via `ReservedAgg` update

---

## 3d. Error Path Scenarios

### EP-1 (Both): Order Not Found
**Given**: `ReservationRepository.findByOrderId("ORD-NOTEXIST")` returns empty

**When**: Release or Cancel request for non-existent order

**Then**:
- [Inferred from typical guard after findByOrderId call]: `IllegalArgumentException` → HTTP 400
- Body: `ErrorResponse { error, message: "Reservation not found for orderId …", requestId }`

### EP-2 (Release): Attempt to Release Already-RELEASED Reservation
**Given**: `Reservation.status=RELEASED`

**When**: `POST /inventory/reservations/release`

**Then**:
- [Inferred from IF status check at line 183]: `IllegalArgumentException` → HTTP 400
- Body: `ErrorResponse { message: "Cannot release a reservation with status RELEASED" }`

### EP-3 (Release): Attempt to Release Already-CANCELLED Reservation
**Given**: `Reservation.status=CANCELLED`

**When**: `POST /inventory/reservations/release`

**Then**:
- HTTP 400 — invalid state transition [Inferred]

### EP-4 (Cancel): Attempt to Cancel Already-CANCELLED Reservation
**Given**: `Reservation.status=CANCELLED`

**When**: `POST /inventory/reservations/cancel`

**Then**:
- [Inferred from IF at line 209]: HTTP 400 — invalid state transition

### EP-5 (Cancel): Attempt to Cancel a RELEASED Reservation
**Given**: `Reservation.status=RELEASED`

**When**: `POST /inventory/reservations/cancel`

**Then**:
- [Inferred]: HTTP 400 — cannot cancel a released reservation

### EP-6 (Both): Missing `orderId` in Request
**Given**: Request body `{}`

**When**: Release or Cancel request

**Then**:
- HTTP 400 — `handleValidation` or `handleIllegalArgument`

### EP-7 (Both): Kafka Failure
**Given**: Kafka unavailable during event send

**When**: Release or Cancel with valid reservation

**Then**:
- [Hypothesized]: Exception propagates → `@Transactional` rollback → HTTP 500
- Reservation status NOT changed

---

## 3e. Edge Cases

### EC-1: ReservedAgg Goes Negative
**Given**: Bug scenario — `ReservedAgg.qtyReserved=10` but reservation `qty=30` (inconsistent state)

**When**: Release executed

**Then**:
- `ReservedAgg.qtyReserved` would become -20 [Observed: formula is `current - qty` at line 194/220]
- Assert: guard exists to prevent negative values OR DB constraint catches it
- This is a critical data integrity edge case

### EC-2: Concurrent Release and Ship for Same Order
**Given**: Release and Ship requests fire simultaneously for `orderId="ORD-001"`

**When**: Both requests execute concurrently

**Then**:
- [Hypothesized]: One succeeds, one fails with invalid state transition (RELEASED can't be shipped)
- `@Transactional` + optimistic locking required to prevent double-update

### EC-3: ReservedAgg Not Found for SKU × Location
**Given**: `ReservedAggRepository.findBySkuAndLocationId` returns empty (data inconsistency)

**When**: Release or Cancel executed

**Then**:
- [Hypothesized]: `NullPointerException` or `IllegalArgumentException` → HTTP 400/500
- Flag as data consistency bug — should be handled gracefully

### EC-4: Redis TTL Key Cleanup After Release/Cancel
**Given**: Soft-hold TTL key still exists in Redis after release/cancel

**When**: TTL expires after reservation already released/cancelled

**Then**:
- `TempReservationExpiryListener` fires and may attempt to release an already-released reservation
- Assert: listener handles idempotently (no double stock release)

---

## 3f. Mock Boundaries

### Release
| Dependency | Method | Happy Path Stub | Error Path Stub |
|---|---|---|---|
| `ReservationRepository` | `findByOrderId(String)` | Return `Optional.of(softHeldReservation)` | Return `Optional.empty()` |
| `ReservedAggRepository` | `findBySkuAndLocationId(String, Long)` | Return agg with `qtyReserved=30` | Return `Optional.empty()` |
| `ReservationRepository` | `save(Reservation)` | Return updated entity | Throw exception |
| `ReservedAggRepository` | `save(ReservedAgg)` | Return updated agg | Throw exception |
| `KafkaProducerService` | `sendReservationReleasedEvent(Reservation)` | No-op | Throw `KafkaException` |

### Cancel
| Dependency | Method | Happy Path Stub | Error Path Stub |
|---|---|---|---|
| `ReservationRepository` | `findByOrderId(String)` | Return `Optional.of(softHeldReservation)` | Return `Optional.empty()` |
| `ReservedAggRepository` | `findBySkuAndLocationId(String, Long)` | Return agg with `qtyReserved=20` | Return `Optional.empty()` |
| `ReservationRepository` | `save(Reservation)` | Return updated entity | Throw exception |
| `ReservedAggRepository` | `save(ReservedAgg)` | Return updated agg | Throw exception |
| `KafkaProducerService` | `sendReservationCancelledEvent(Reservation)` | No-op | Throw `KafkaException` |
