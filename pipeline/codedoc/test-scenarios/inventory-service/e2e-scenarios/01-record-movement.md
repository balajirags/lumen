---
title: "E2E Scenarios — Record Inventory Movement"
type: "e2e-test-scenario"
flow: "record-inventory-movement"
entry_point: "com.inventory.inventoryservice.controller.InventoryController.recordMovement(MovementRequest)"
evidence: "Observed"
timestamp: "2025-01-24T00:00:00Z"
---

# Flow: Record Inventory Movement

## 3a. Flow Summary [Observed]

| Attribute | Value |
|---|---|
| **Entry Point** | `POST /inventory/movements` (inferred path) |
| **Controller Method** | `InventoryController.recordMovement(MovementRequest)` — line 35 |
| **Returns** | `ResponseEntity<MovementLedger>` |
| **Description** | Records a stock movement (receipt, sale, adjustment, etc.) against a SKU at a warehouse location. Updates `InventoryState` and writes to `MovementLedger`. Emits a Kafka event. |
| **Layers Traversed** | `InventoryController` → `MovementService.recordMovement(...)` → `InventoryStateRepository` + `MovementLedgerRepository` → DB → `KafkaProducerService.sendMovementRecordedEvent` |
| **Transaction** | `@Transactional` on `MovementService.recordMovement` |
| **Kafka Event** | `MOVEMENT_RECORDED_TOPIC` |

### Call Chain (Observed)
```
InventoryController.recordMovement(MovementRequest)
  └─ MovementService.recordMovement(sku, locationId, qty, type, referenceId, source) [@Transactional]
       ├─ MovementLedger entity constructed & populated (lines 35-42)
       ├─ InventoryStateRepository.findBySkuAndLocationId(sku, locationId) (line 48)
       ├─ InventoryState updated via SWITCH on MovementType (line 56)
       ├─ MovementLedgerRepository.save(ledger) (inferred from line 66-71)
       └─ KafkaProducerService.sendMovementRecordedEvent(MovementLedger) (line 71)
```

---

## 3b. Test Data Setup

### Required DB State
- `InventoryState` row for `(sku="SKU-001", locationId=1)` must exist (or the service creates it — [Hypothesized: upsert logic based on findBySkuAndLocationId returning Optional])
- `Location` with `id=1` must exist (referential integrity)

### Input Payload — `MovementRequest`
```json
{
  "sku": "SKU-001",
  "locationId": 1,
  "qty": 50,
  "type": "RECEIPT",
  "referenceId": "PO-2024-001",
  "source": "WMS"
}
```

### Field Types [Observed]
| Field | Type | Required |
|---|---|---|
| `sku` | `String` | Yes |
| `locationId` | `Long` | Yes |
| `qty` | `Integer` | Yes |
| `type` | `MovementLedger.MovementType` (enum) | Yes |
| `referenceId` | `String` | Yes (assumed) |
| `source` | `String` | Yes (assumed) |

---

## 3c. Happy Path Scenarios

### HP-1: Record a Receipt (Inbound Stock)
**Given**:
- `InventoryState(sku="SKU-001", locationId=1)` exists with `onHand=100`
- Valid `MovementRequest` with `type=RECEIPT`, `qty=50`

**When**: `POST /inventory/movements` with the request body above

**Then**:
- HTTP 200 OK (inferred; controller returns `ResponseEntity<MovementLedger>`)
- Response body is a `MovementLedger` with `sku="SKU-001"`, `locationId=1`, `qty=50`, `type="RECEIPT"`, `referenceId="PO-2024-001"`, `source="WMS"`, `createdAt` set
- `InventoryState.onHand` is incremented by 50 (→ 150) [Inferred from SWITCH statement at line 56 handling movement types]
- A new `MovementLedger` row is persisted in DB
- Kafka event published to `MOVEMENT_RECORDED_TOPIC`

### HP-2: Record a Shipment / Sale (Stock Out)
**Given**:
- `InventoryState(sku="SKU-002", locationId=2)` exists with `onHand=200`
- Valid `MovementRequest` with `type=SHIPMENT` (or equivalent OUTBOUND enum value), `qty=30`

**When**: `POST /inventory/movements`

**Then**:
- HTTP 200 OK
- `InventoryState.onHand` decremented by 30 (→ 170) [Inferred]
- `MovementLedger` row persisted
- Kafka event published

### HP-3: Record an Adjustment (Quarantine / Damage)
**Given**:
- `InventoryState` exists
- `MovementRequest` with `type=DAMAGE` (or similar), `qty=5`

**When**: `POST /inventory/movements`

**Then**:
- HTTP 200 OK
- `InventoryState.damaged` or `InventoryState.quarantine` updated accordingly [Inferred from distinct fields on `InventoryState`]
- `MovementLedger` persisted, Kafka event published

### HP-4: New SKU at New Location (InventoryState auto-created)
**Given**:
- No `InventoryState` row for `(sku="SKU-NEW", locationId=5)`

**When**: `POST /inventory/movements` with `sku="SKU-NEW"`, `locationId=5`

**Then**:
- HTTP 200 OK [Hypothesized: upsert creates new InventoryState if findBySkuAndLocationId returns empty]
- `InventoryState` created for that SKU × location
- `MovementLedger` row persisted

---

## 3d. Error Path Scenarios

### EP-1: Invalid MovementType Enum Value
**Given**: Request body contains `"type": "INVALID_TYPE"`

**When**: `POST /inventory/movements`

**Then**:
- HTTP 400 Bad Request
- `GlobalExceptionHandler.handleMalformedJson` fires (`HttpMessageNotReadableException`)
- Body: `{ "error": "BAD_REQUEST", "message": "<deserialization error>", "requestId": "<id>" }`

### EP-2: Missing Required Field (`sku` omitted)
**Given**: Payload missing `sku` field (or null sku), Bean Validation `@NotBlank`/`@NotNull` assumed [Hypothesized]

**When**: `POST /inventory/movements`

**Then**:
- HTTP 400 Bad Request
- `GlobalExceptionHandler.handleValidation` fires
- Body: `ErrorResponse` with field-level validation messages

### EP-3: Malformed JSON Body
**Given**: Request body is not valid JSON, e.g. `{sku: SKU-001}`

**When**: `POST /inventory/movements` with `Content-Type: application/json`

**Then**:
- HTTP 400 Bad Request — `handleMalformedJson`

### EP-4: Wrong Content-Type
**Given**: Request sent with `Content-Type: text/plain`

**When**: `POST /inventory/movements`

**Then**:
- HTTP 415 Unsupported Media Type — `handleUnsupportedMediaType`

### EP-5: Payload Too Large
**Given**: Request body exceeds the configured `maxBytes` threshold

**When**: `POST /inventory/movements`

**Then**:
- HTTP 413 Payload Too Large — `handlePayloadTooLarge`
- `PayloadTooLargeException.maxBytes` included in message

### EP-6: Kafka Publish Failure
**Given**: Kafka broker unavailable during `sendMovementRecordedEvent`

**When**: `POST /inventory/movements` with valid payload

**Then**:
- [Hypothesized]: DB transaction may have already committed; behaviour depends on whether Kafka failure throws or is async. Flag for review — test for either rollback or partial success.
- If exception propagates: HTTP 500, `handleGeneral` fires

---

## 3e. Edge Cases

### EC-1: Zero Quantity Movement
**Given**: `MovementRequest` with `qty=0`

**When**: `POST /inventory/movements`

**Then**:
- [Hypothesized]: May succeed and create a zero-delta ledger entry, or fail validation
- Assert: `InventoryState` unchanged, `MovementLedger` entry has `qty=0` OR validation error returned

### EC-2: Negative Quantity Movement
**Given**: `qty=-10`

**When**: `POST /inventory/movements`

**Then**:
- [Hypothesized]: Validation error (HTTP 400) if `@Min(1)` is applied, or accepted as a reversal
- Assert expected behaviour clearly in implementation

### EC-3: Very Large Quantity
**Given**: `qty=Integer.MAX_VALUE` (2,147,483,647)

**When**: `POST /inventory/movements`

**Then**:
- [Hypothesized]: May cause integer overflow in `InventoryState` counters
- Assert DB constraint or business rule prevents overflow

### EC-4: Duplicate `referenceId`
**Given**: A `MovementLedger` with `referenceId="PO-2024-001"` already exists

**When**: `POST /inventory/movements` with same `referenceId`

**Then**:
- [Hypothesized]: Accepted (no unique constraint observed on referenceId); second ledger entry created
- Verify idempotency requirements with team

### EC-5: Non-existent LocationId
**Given**: `locationId=9999` does not exist in `Location` table

**When**: `POST /inventory/movements`

**Then**:
- [Hypothesized]: If FK constraint exists → DB error → HTTP 500 or 400 via `handleIllegalArgument` / `handleGeneral`
- If no FK: `InventoryState` created with orphaned locationId

---

## 3f. Mock Boundaries

| Dependency | Method | Happy Path Stub | Error Path Stub |
|---|---|---|---|
| `InventoryStateRepository` | `findBySkuAndLocationId(String, Long)` | Return `Optional.of(existingState)` | Return `Optional.empty()` to test auto-create |
| `MovementLedgerRepository` | `save(MovementLedger)` | Return saved entity with generated `id` and `createdAt` | Throw `DataIntegrityViolationException` |
| `KafkaProducerService` | `sendMovementRecordedEvent(MovementLedger)` | No-op / void | Throw `KafkaException` |

### Isolation Notes
- In controller-layer tests: mock `MovementService` entirely
- In service-layer tests: mock `InventoryStateRepository`, `MovementLedgerRepository`, `KafkaProducerService`
- Full integration test: real DB (Testcontainers), mock or embedded Kafka
