---
title: "E2E Scenarios — Confirm Reservation"
type: "e2e-test-scenario"
flow: "confirm-reservation"
entry_point: "com.inventory.inventoryservice.controller.InventoryController.confirmReservation(ReservationConfirmRequest)"
evidence: "Observed"
timestamp: "2025-01-24T00:00:00Z"
---

# Flow: Confirm Reservation

## 3a. Flow Summary [Observed]

| Attribute | Value |
|---|---|
| **Entry Point** | `POST /inventory/reservations/confirm` (inferred path) |
| **Controller Method** | `InventoryController.confirmReservation(ReservationConfirmRequest)` — line 88 |
| **Returns** | `ResponseEntity<Reservation>` |
| **Description** | Upgrades an existing soft-held reservation to CONFIRMED status. The `ReservationConfirmRequest` provides the definitive sku, locationId, and qty that may differ from the original soft hold (re-confirmation scenario). Multiple IF branches (lines 112, 123, 137) handle status validation and whether a new Reservation entity is created vs updated. |
| **Layers Traversed** | `InventoryController` → `ReservationService.confirmReservation(ReservationConfirmRequest)` → `ReservationRepository` + `ReservedAggRepository` → DB → `KafkaProducerService` |
| **Transaction** | `@Transactional` on `ReservationService.confirmReservation` |
| **Kafka Event** | `RESERVATION_CONFIRMED_TOPIC` (via `sendReservationConfirmedEvent`) |

### Call Chain (Observed)
```
InventoryController.confirmReservation(ReservationConfirmRequest)
  └─ ReservationService.confirmReservation(ReservationConfirmRequest) [@Transactional]
       ├─ ReservationRepository.findByOrderId(orderId) (line 122)
       ├─ IF line 123: status check (e.g., already CONFIRMED or CANCELLED?)
       ├─ getStockComponentsFromDB(sku, locationId) (line 131)
       │    ├─ InventoryStateRepository.findBySkuAndLocationId
       │    ├─ ReservedAggRepository.findBySkuAndLocationId
       │    └─ SafetyStockPolicyRepository.findActivePolicy
       ├─ IF line 112: null/not-found check
       ├─ IF line 137: stock adequacy check
       ├─ Reservation entity set: sku, locationId, qty, orderId, status=CONFIRMED, createdAt (lines 147-154)
       ├─ ReservedAgg: findBySkuAndLocationId (line 158), then updated (lines 161-169)
       ├─ ReservationRepository.save / ReservedAggRepository.save (inferred)
       └─ KafkaProducerService.sendReservationConfirmedEvent(Reservation) (line 173)
```

---

## 3b. Test Data Setup

### Required DB State
- Existing `Reservation` with `orderId="ORD-001"`, `status=SOFT_HELD`
- `InventoryState(sku="SKU-001", locationId=1)` with `onHand=100`
- `ReservedAgg(sku="SKU-001", locationId=1)` with `qtyReserved=30` (reflecting the soft hold)
- Optionally: active `SafetyStockPolicy`

### Input Payload — `ReservationConfirmRequest`
```json
{
  "orderId": "ORD-001",
  "sku": "SKU-001",
  "locationId": 1,
  "qty": 30
}
```

### Field Types [Observed]
| Field | Type | Notes |
|---|---|---|
| `orderId` | `String` | Required — identifies existing reservation |
| `sku` | `String` | Required — final committed SKU |
| `locationId` | `Long` | Required — final committed location |
| `qty` | `Integer` | Required — final committed quantity |

---

## 3c. Happy Path Scenarios

### HP-1: Confirm Existing SOFT_HELD Reservation (Same Details)
**Given**:
- Reservation exists: `orderId="ORD-001"`, `status=SOFT_HELD`, `sku="SKU-001"`, `locationId=1`, `qty=30`
- `InventoryState.onHand=100`, `ReservedAgg.qtyReserved=30`
- Confirm request matches the soft hold exactly

**When**: `POST /inventory/reservations/confirm`

**Then**:
- HTTP 200 OK
- Response body: `Reservation` with `status=CONFIRMED`
- `Reservation.status` updated to `CONFIRMED` in DB
- `ReservedAgg.qtyReserved` remains 30 (no change if qty unchanged) [Inferred]
- Redis TTL key deleted or left to expire (soft hold no longer active) [Hypothesized]
- Kafka event published to `RESERVATION_CONFIRMED_TOPIC`

### HP-2: Confirm with Amended Quantity (Downward Adjustment)
**Given**:
- Soft hold was for `qty=30`; confirm request has `qty=20`

**When**: `POST /inventory/reservations/confirm`

**Then**:
- HTTP 200 OK
- `Reservation.qty` updated to 20
- `ReservedAgg.qtyReserved` decremented by 10 (from 30 → 20) [Inferred from lines 163/166]
- Kafka event published

### HP-3: Re-confirm (Idempotent Call on Already-CONFIRMED)
**Given**: Reservation is already `CONFIRMED`

**When**: `POST /inventory/reservations/confirm` (re-confirm)

**Then**:
- [Inferred from IF at line 123]: Either idempotent success (200, no change) or business error (400)
- Flag for exact behaviour verification with team

### HP-4: Confirm with New Reservation Created (No Prior Soft Hold)
**Given**:
- `ReservationRepository.findByOrderId("ORD-005")` returns empty
- Sufficient stock available
- IF line 112 path: new reservation entity created

**When**: `POST /inventory/reservations/confirm`

**Then**:
- HTTP 200 OK
- New `Reservation` created directly in `CONFIRMED` state [Inferred from IF branch at line 137]

---

## 3d. Error Path Scenarios

### EP-1: Order Not Found
**Given**: `ReservationRepository.findByOrderId("ORD-NOTEXIST")` returns empty (IF null check at line 112)

**When**: `POST /inventory/reservations/confirm`

**Then**:
- [Inferred from IF at line 112]: `IllegalArgumentException` → HTTP 400 via `handleIllegalArgument`
- Or [Hypothesized]: HTTP 404 Not Found if a custom exception is used

### EP-2: Reservation Already Cancelled / Released
**Given**: Reservation exists with `status=CANCELLED` or `status=RELEASED`

**When**: `POST /inventory/reservations/confirm`

**Then**:
- [Inferred from IF at line 123 + status check]: `IllegalArgumentException` thrown → HTTP 400
- Body: `ErrorResponse { error, message: "Cannot confirm reservation in status CANCELLED", requestId }`

### EP-3: Insufficient Stock for Confirmation Quantity
**Given**:
- Soft hold was `qty=30`, but available stock since decreased
- `onHand=25`, `qtyReserved=25`, `safetyStock=10` → ATP < 30

**When**: `POST /inventory/reservations/confirm` with `qty=30`

**Then**:
- [Inferred from IF at line 137]: `IllegalArgumentException` → HTTP 400
- Existing reservation not modified; no Kafka event

### EP-4: Missing Required Fields in Request
**Given**: `ReservationConfirmRequest` missing `sku` or `orderId`

**When**: `POST /inventory/reservations/confirm`

**Then**:
- HTTP 400 — `handleValidation` (Bean Validation) or `handleMalformedJson`

### EP-5: Kafka Publish Failure
**Given**: Kafka unavailable when `sendReservationConfirmedEvent` called

**When**: `POST /inventory/reservations/confirm`

**Then**:
- [Hypothesized]: `@Transactional` rollback if exception propagates → HTTP 500
- Reservation status NOT updated to CONFIRMED

---

## 3e. Edge Cases

### EC-1: Confirm with Upward Quantity Adjustment
**Given**: Soft hold for `qty=20`, confirm request for `qty=50`
**When**: `POST /inventory/reservations/confirm`
**Then**:
- Additional stock check required (ATP must cover the additional qty)
- `ReservedAgg.qtyReserved` incremented to 50 [Inferred]
- Fails with HTTP 400 if insufficient stock

### EC-2: Confirm After Redis TTL Expiry
**Given**:
- Soft hold TTL has expired; `TempReservationExpiryListener` may have set status to RELEASED
- Client attempts to confirm after expiry

**When**: `POST /inventory/reservations/confirm`

**Then**:
- [Inferred]: Status check finds RELEASED → HTTP 400 "Cannot confirm RELEASED reservation"

### EC-3: Concurrent Confirmations for Same Order
**Given**: Two simultaneous confirm requests for `orderId="ORD-001"`

**When**: Both fire simultaneously

**Then**:
- `@Transactional` ensures one wins; the other sees an already-CONFIRMED state
- Assert: exactly one Kafka event emitted, DB state consistent

### EC-4: sku / locationId in Confirm Different from Soft Hold
**Given**:
- Soft hold: `sku="SKU-A"`, `locationId=1`
- Confirm: `sku="SKU-B"`, `locationId=2`

**When**: `POST /inventory/reservations/confirm`

**Then**:
- [Inferred from Reservation fields being set from request, lines 147-150]: New values applied — test confirms substitution behaviour or business rule prevents cross-SKU confirmation

---

## 3f. Mock Boundaries

| Dependency | Method | Happy Path Stub | Error Path Stub |
|---|---|---|---|
| `ReservationRepository` | `findByOrderId(String)` | Return `Optional.of(softHeldReservation)` | Return `Optional.empty()` |
| `InventoryStateRepository` | `findBySkuAndLocationId(String, Long)` | Return state with sufficient `onHand` | Return `Optional.empty()` |
| `ReservedAggRepository` | `findBySkuAndLocationId(String, Long)` | Return agg entity (line 158) | Return `Optional.empty()` → auto-create |
| `SafetyStockPolicyRepository` | `findActivePolicy(String, Long, OffsetDateTime)` | Return policy or empty | Return policy that consumes all ATP |
| `ReservationRepository` | `save(Reservation)` | Return updated entity | Throw `DataIntegrityViolationException` |
| `ReservedAggRepository` | `save(ReservedAgg)` | Return updated agg | Throw `DataIntegrityViolationException` |
| `KafkaProducerService` | `sendReservationConfirmedEvent(Reservation)` | No-op | Throw `KafkaException` |
