---
title: "E2E Scenarios — Ship Order"
type: "e2e-test-scenario"
flow: "ship-order"
entry_point: "com.inventory.inventoryservice.controller.InventoryController.ship(ShipmentRequest)"
evidence: "Observed"
timestamp: "2025-01-24T00:00:00Z"
---

# Flow: Ship Order

## 3a. Flow Summary [Observed]

| Attribute | Value |
|---|---|
| **Entry Point** | `POST /inventory/ship` (inferred path) |
| **Controller Method** | `InventoryController.ship(ShipmentRequest)` — line 124 |
| **Returns** | `ResponseEntity<Void>` |
| **Description** | Fulfils a confirmed reservation by physically reducing on-hand stock and marking the reservation as SHIPPED (final state). Looks up reservations by orderId, validates status, reduces `ReservedAgg`, records movement(s), and publishes Kafka event. This is the terminal success state for a reservation lifecycle. |
| **Layers Traversed** | `InventoryController` → `InventoryService.ship(orderId)` → `ReservationRepository` + `ReservedAggRepository` → `MovementService.recordMovement(...)` → `InventoryStateRepository` + `MovementLedgerRepository` → DB → `KafkaProducerService` |
| **Transaction** | `@Transactional` on `InventoryService.ship` |
| **Kafka Events** | `MOVEMENT_RECORDED_TOPIC` (via `MovementService.recordMovement`) |
| **Annotations** | `@ApiResponse` on controller method |

### Call Chain (Observed)
```
InventoryController.ship(ShipmentRequest)
  └─ InventoryService.ship(orderId) [@Transactional]
       ├─ ReservationRepository.findByOrderId(orderId)              (line 57)
       ├─ IF line 60: status checks (CONFIRMED required? SHIPPED already?)
       │    [checks reservation.getStatus() twice — lines 60, 61]
       ├─ ReservedAggRepository.findBySkuAndLocationId(sku, locationId)  (line 65)
       ├─ ReservedAgg.getQtyReserved() − reservation.getQty()           (line 68)
       ├─ ReservedAgg.setQtyReserved(updated value)                     (line 68)
       ├─ Reservation.setStatus(SHIPPED / terminal state)               (line 75)
       ├─ MovementService.recordMovement(sku, locationId, qty, <SHIPMENT_TYPE>, orderId, source) (line 72)
       │    ├─ InventoryStateRepository.findBySkuAndLocationId
       │    ├─ InventoryState.onHand decremented (SWITCH)
       │    ├─ MovementLedgerRepository.save
       │    └─ KafkaProducerService.sendMovementRecordedEvent
       └─ ReservationRepository.save / ReservedAggRepository.save  (inferred)
```

---

## 3b. Test Data Setup

### Required DB State
- `Reservation` with `orderId="ORD-001"`, `status=CONFIRMED`, `sku="SKU-001"`, `locationId=1`, `qty=30`
- `InventoryState(sku="SKU-001", locationId=1)` with `onHand=100`
- `ReservedAgg(sku="SKU-001", locationId=1)` with `qtyReserved=30`

### Input Payload — `ShipmentRequest`
```json
{
  "orderId": "ORD-001"
}
```

### Field Types [Observed]
| Field | Type | Notes |
|---|---|---|
| `orderId` | `String` | Required — identifies the confirmed reservation to ship |

---

## 3c. Happy Path Scenarios

### HP-1: Ship a CONFIRMED Reservation
**Given**:
- `Reservation(orderId="ORD-001", status=CONFIRMED, qty=30, sku="SKU-001", locationId=1)`
- `InventoryState.onHand=100`, `ReservedAgg.qtyReserved=30`

**When**: `POST /inventory/ship` with `{ "orderId": "ORD-001" }`

**Then**:
- HTTP 204 No Content (inferred — `ResponseEntity<Void>`)
- `Reservation.status` = SHIPPED (final state) [Inferred from `setStatus` at line 75]
- `ReservedAgg.qtyReserved` decremented by 30 (→ 0) [Observed lines 65-68]
- `InventoryState.onHand` decremented by 30 (→ 70) [Inferred from `MovementService.recordMovement` with shipment type]
- `MovementLedger` row persisted for the outbound movement
- Kafka event published to `MOVEMENT_RECORDED_TOPIC`

### HP-2: Ship Order with Multiple Reserved Items
**Given**:
- Single reservation `orderId="ORD-002"` referencing multiple SKUs [Hypothesized — if multi-line reservations supported; actual structure uses single sku/locationId per Reservation]
- If single-line: only one sku/location updated

**When**: `POST /inventory/ship`

**Then**:
- All items shipped, `ReservedAgg` and `InventoryState` updated for each

### HP-3: Ship Order — ReservedAgg Goes to Zero
**Given**: Last reservation for this SKU × location; `qtyReserved=30`, `qty=30`

**When**: `POST /inventory/ship`

**Then**:
- `ReservedAgg.qtyReserved` = 0
- `InventoryState.onHand` decremented
- No negative values

---

## 3d. Error Path Scenarios

### EP-1: Order Not Found
**Given**: `ReservationRepository.findByOrderId("ORD-NOTEXIST")` returns empty

**When**: `POST /inventory/ship`

**Then**:
- [Inferred from IF line 60 null check]: `IllegalArgumentException` → HTTP 400 via `handleIllegalArgument`
- Body: `ErrorResponse { message: "No reservation found for orderId …" }`
- No stock changes, no Kafka events

### EP-2: Reservation Not in CONFIRMED Status (e.g., SOFT_HELD)
**Given**: `Reservation.status=SOFT_HELD`

**When**: `POST /inventory/ship`

**Then**:
- [Inferred from double status check at lines 60-61]: `IllegalArgumentException` → HTTP 400
- Body: `ErrorResponse { message: "Cannot ship reservation in status SOFT_HELD …" }`

### EP-3: Reservation Already SHIPPED
**Given**: `Reservation.status=SHIPPED` (terminal)

**When**: `POST /inventory/ship` again

**Then**:
- [Inferred from status check at line 61]: `IllegalArgumentException` → HTTP 400
- Idempotency guard prevents double-shipment

### EP-4: Reservation RELEASED or CANCELLED
**Given**: `Reservation.status=RELEASED` or `CANCELLED`

**When**: `POST /inventory/ship`

**Then**:
- HTTP 400 — cannot ship a released or cancelled reservation

### EP-5: ReservedAgg Not Found
**Given**: `ReservedAggRepository.findBySkuAndLocationId` returns empty (data inconsistency)

**When**: `POST /inventory/ship`

**Then**:
- [Hypothesized]: `NullPointerException` or exception from accessing `.getQtyReserved()` on null
- HTTP 500 via `handleGeneral`
- Flag as data integrity scenario to test and harden

### EP-6: InventoryState onHand Goes Negative
**Given**: `InventoryState.onHand=10`, but reservation `qty=30` (data inconsistency)

**When**: `POST /inventory/ship`

**Then**:
- [Hypothesized]: onHand becomes -20 if no guard exists
- Assert: business rule or DB constraint prevents negative stock
- Critical data integrity check

### EP-7: Missing `orderId` in Request
**Given**: `{ }` or `{ "orderId": null }`

**When**: `POST /inventory/ship`

**Then**:
- HTTP 400 — `handleValidation` or `handleIllegalArgument`

---

## 3e. Edge Cases

### EC-1: Concurrent Ship Requests for Same Order
**Given**: Two simultaneous ship requests for `orderId="ORD-001"`

**When**: Both fire simultaneously

**Then**:
- `@Transactional` + DB row locking: one succeeds, second sees SHIPPED status → HTTP 400
- Assert: stock decremented exactly once, one `MovementLedger` row, one Kafka event

### EC-2: Ship After Release (Race Condition)
**Given**:
- Thread 1: Release request for `orderId="ORD-001"` (sets status=RELEASED)
- Thread 2: Ship request for `orderId="ORD-001"` races against release

**When**: Both execute near-simultaneously

**Then**:
- One wins; other sees final status and rejects
- Assert: no partial state — either shipped (SHIPPED, stock decremented) or released (RELEASED, stock returned)

### EC-3: Ship with Zero qty Reservation
**Given**: `Reservation.qty=0`

**When**: `POST /inventory/ship`

**Then**:
- [Hypothesized]: `RecordMovement` called with `qty=0`; `onHand` unchanged; ledger entry with `qty=0` created
- Verify if zero-qty shipments are valid business events

### EC-4: ReservedAgg Underflow
**Given**: `ReservedAgg.qtyReserved=10` but `reservation.qty=30` (inconsistency)

**When**: `POST /inventory/ship`

**Then**:
- `ReservedAgg.qtyReserved` = -20 [Observed formula at line 68]
- Assert: guard or DB CHECK constraint prevents this
- Critical: test for `Math.max(0, current - qty)` or similar protection

---

## 3f. Mock Boundaries

| Dependency | Method | Happy Path Stub | Error Path Stub |
|---|---|---|---|
| `ReservationRepository` | `findByOrderId(String)` | Return `Optional.of(confirmedReservation)` | Return `Optional.empty()` |
| `ReservedAggRepository` | `findBySkuAndLocationId(String, Long)` | Return agg with `qtyReserved=30` | Return `Optional.empty()` |
| `ReservationRepository` | `save(Reservation)` | Return updated entity with SHIPPED status | Throw `DataIntegrityViolationException` |
| `ReservedAggRepository` | `save(ReservedAgg)` | Return updated agg | Throw exception |
| `MovementService` | `recordMovement(String, Long, Integer, MovementLedger.MovementType, String, String)` | Return `MovementLedger` entity | Throw `IllegalArgumentException` |
| `InventoryStateRepository` | `findBySkuAndLocationId(String, Long)` | Return state with `onHand=100` | Return `Optional.empty()` |
| `MovementLedgerRepository` | `save(MovementLedger)` | Return persisted entity | Throw `DataIntegrityViolationException` |
| `KafkaProducerService` | `sendMovementRecordedEvent(MovementLedger)` | No-op | Throw `KafkaException` |
