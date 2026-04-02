---
title: "E2E Scenarios — Transfer Inventory Between Locations"
type: "e2e-test-scenario"
flow: "transfer-inventory"
entry_point: "com.inventory.inventoryservice.controller.InventoryController.transferInventory(TransferRequest)"
evidence: "Observed"
timestamp: "2025-01-24T00:00:00Z"
---

# Flow: Transfer Inventory Between Locations

## 3a. Flow Summary [Observed]

| Attribute | Value |
|---|---|
| **Entry Point** | `POST /inventory/transfer` (inferred path) |
| **Controller Method** | `InventoryController.transferInventory(TransferRequest)` — line 72 |
| **Returns** | `ResponseEntity<Void>` |
| **Description** | Moves a quantity of a SKU from a source location to a destination location. Internally calls `recordMovement` twice: once as a stock-out at source and once as a stock-in at destination. |
| **Layers Traversed** | `InventoryController` → `MovementService.executeTransfer(TransferRequest)` → `MovementService.recordMovement(...)` ×2 → `InventoryStateRepository` + `MovementLedgerRepository` → DB → `KafkaProducerService.sendMovementRecordedEvent(...)` ×2 |
| **Transaction** | `@Transactional` on `MovementService.executeTransfer` |
| **Kafka Events** | `MOVEMENT_RECORDED_TOPIC` (published twice — once per leg) |
| **Annotations** | `@ApiResponse` on controller method |

### Call Chain (Observed)
```
InventoryController.transferInventory(TransferRequest)
  └─ MovementService.executeTransfer(TransferRequest) [@Transactional]
       ├─ MovementService.recordMovement(sku, sourceLocationId, qty, <OUT_TYPE>, ...) (line 90)
       └─ MovementService.recordMovement(sku, destinationLocationId, qty, <IN_TYPE>, ...) (line 99)
            ├─ InventoryStateRepository.findBySkuAndLocationId(...) ×2
            ├─ InventoryState updated (SWITCH on MovementType) ×2
            ├─ MovementLedgerRepository.save(...) ×2
            └─ KafkaProducerService.sendMovementRecordedEvent(...) ×2
```

---

## 3b. Test Data Setup

### Required DB State
- `Location` rows exist for both `sourceLocationId=1` and `destinationLocationId=2`
- `InventoryState(sku="SKU-001", locationId=1)` with sufficient `onHand` (e.g., 100) for the transfer quantity
- `InventoryState(sku="SKU-001", locationId=2)` may or may not exist (destination may be auto-created)

### Input Payload — `TransferRequest`
```json
{
  "sku": "SKU-001",
  "sourceLocationId": 1,
  "destinationLocationId": 2,
  "quantity": 30
}
```

### Field Types [Observed]
| Field | Type | Notes |
|---|---|---|
| `sku` | `String` | Required |
| `sourceLocationId` | `Long` | Required |
| `destinationLocationId` | `Long` | Required |
| `quantity` | `Integer` | Required |

---

## 3c. Happy Path Scenarios

### HP-1: Standard Inter-Location Transfer
**Given**:
- Source `InventoryState(sku="SKU-001", locationId=1)` with `onHand=100`
- Destination `InventoryState(sku="SKU-001", locationId=2)` with `onHand=50`
- Both locations active

**When**: `POST /inventory/transfer` with `quantity=30`

**Then**:
- HTTP 204 No Content (inferred — `ResponseEntity<Void>`)
- Source `InventoryState.onHand` decremented by 30 (→ 70) [Inferred]
- Destination `InventoryState.onHand` incremented by 30 (→ 80) [Inferred]
- Two `MovementLedger` rows persisted (one per leg)
- Two Kafka events published to `MOVEMENT_RECORDED_TOPIC`

### HP-2: Transfer to New Destination Location (No Existing InventoryState)
**Given**:
- Source `InventoryState` exists with `onHand=200`
- No `InventoryState` exists at `destinationLocationId`

**When**: `POST /inventory/transfer` with `quantity=50`

**Then**:
- HTTP 204 No Content
- Source stock decremented by 50
- New `InventoryState` created at destination with `onHand=50` [Hypothesized: auto-create via upsert]
- Two `MovementLedger` rows persisted

### HP-3: Transfer Full Stock (onHand goes to zero)
**Given**:
- Source `InventoryState.onHand=30`

**When**: `POST /inventory/transfer` with `quantity=30`

**Then**:
- HTTP 204 No Content
- Source `onHand` becomes 0
- Destination `onHand` increases by 30

---

## 3d. Error Path Scenarios

### EP-1: Insufficient Stock at Source
**Given**:
- Source `InventoryState.onHand=10`
- `quantity=50` (exceeds available stock)

**When**: `POST /inventory/transfer`

**Then**:
- [Hypothesized]: `IllegalArgumentException` thrown → HTTP 400 via `handleIllegalArgument`
- Or DB constraint violation if negative stock is not allowed
- No `MovementLedger` rows persisted (transaction rolled back)
- No Kafka events published

### EP-2: Same Source and Destination Location
**Given**: `sourceLocationId` == `destinationLocationId`

**When**: `POST /inventory/transfer`

**Then**:
- [Hypothesized]: Business rule validation → `IllegalArgumentException` → HTTP 400
- Or two ledger entries cancel out with no net effect (if no guard exists)
- Flag for review with team

### EP-3: Non-existent Source LocationId
**Given**: `sourceLocationId=9999` does not exist

**When**: `POST /inventory/transfer`

**Then**:
- [Hypothesized]: If `InventoryState` lookup returns empty and no guard exists → stock goes negative or new zero-state created — likely a bug path to verify
- Expected safe behaviour: HTTP 400 or 404 with descriptive error

### EP-4: Missing Required Fields
**Given**: `TransferRequest` missing `sku` or `quantity`

**When**: `POST /inventory/transfer`

**Then**:
- HTTP 400 — `handleValidation` fires if `@Valid` + Bean Validation annotations applied [Hypothesized]

### EP-5: Kafka Publish Failure on First Event
**Given**: Kafka fails after first `sendMovementRecordedEvent` call

**When**: `POST /inventory/transfer`

**Then**:
- [Hypothesized]: If exception propagates → full `@Transactional` rollback → neither leg committed
- Assert: both `InventoryState` values unchanged, no `MovementLedger` rows

---

## 3e. Edge Cases

### EC-1: Transfer Zero Quantity
**Given**: `quantity=0`

**When**: `POST /inventory/transfer`

**Then**:
- [Hypothesized]: `@Min(1)` validation error (HTTP 400) or two zero-delta ledger entries created
- Verify with team

### EC-2: Transfer Quantity Exceeds Integer Bounds
**Given**: `quantity=2147483647` (MAX_VALUE)

**When**: `POST /inventory/transfer`

**Then**:
- [Hypothesized]: Potential integer overflow in `InventoryState` counters
- Assert guard exists or DB constraint prevents

### EC-3: Concurrent Transfers of Same SKU
**Given**: Two simultaneous transfer requests both referencing `sourceLocationId=1, sku="SKU-001"`

**When**: Both requests fire simultaneously

**Then**:
- [Hypothesized]: `@Transactional` provides serialisation; one may fail or face lock contention
- Assert final `onHand` at source = initial − (qty1 + qty2), or one fails cleanly

### EC-4: Transaction Atomicity — Partial Failure Between Legs
**Given**: DB write succeeds for stock-out leg but fails for stock-in leg

**When**: `POST /inventory/transfer`

**Then**:
- `@Transactional` must roll back both legs
- Assert: source `onHand` unchanged, destination `onHand` unchanged, no `MovementLedger` rows for either leg

---

## 3f. Mock Boundaries

| Dependency | Method | Happy Path Stub | Error Path Stub |
|---|---|---|---|
| `InventoryStateRepository` | `findBySkuAndLocationId(String, Long)` | Return source state with adequate `onHand`, return empty for destination | Return empty for source (no-stock scenario) |
| `MovementLedgerRepository` | `save(MovementLedger)` | Return persisted entity | Throw `DataIntegrityViolationException` on second call |
| `KafkaProducerService` | `sendMovementRecordedEvent(MovementLedger)` | No-op | Throw `KafkaException` on first call to test rollback |

### Isolation Notes
- Controller test: mock `MovementService.executeTransfer` — verify it is called with correct `TransferRequest`
- Service test: mock `InventoryStateRepository`, `MovementLedgerRepository`, `KafkaProducerService` — verify two `recordMovement` calls happen with correct parameters
- Integration test: real DB; use Testcontainers; verify DB state before and after
