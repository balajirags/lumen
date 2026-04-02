---
title: "E2E Scenarios — Inventory State Queries and Reconciliation"
type: "e2e-test-scenario"
flow: "inventory-queries"
entry_point: "com.inventory.inventoryservice.controller.InventoryController (getInventoryState, getReconciliationSnapshot, getMovements, getDistinctSkus, getReservationStatus, getReservations)"
evidence: "Observed"
timestamp: "2025-01-24T00:00:00Z"
---

# Flow: Inventory State Queries and Reconciliation

## 3a. Flow Summary [Observed]

This document covers all read-only query endpoints on `InventoryController` plus utility endpoints.

### Endpoints Covered

| Method | Return Type | Description |
|---|---|---|
| `GET` `getInventoryState(sku, locationId)` | `InventoryStateResponse` | Current stock breakdown for a SKU × location |
| `GET` `getReconciliationSnapshot(sku, locationId)` | `ReconciliationSnapshotResponse` | Snapshot: onHand, qtyReserved, safetyStock |
| `GET` `getMovements(sku, locationId, from, to, pageable)` | `Page<MovementLedger>` | Paginated movement ledger history |
| `GET` `getDistinctSkus()` | `List<String>` | All unique SKUs with any inventory |
| `GET` `getReservationStatus(orderId)` | `ReservationStatusResponse` | Latest reservation status for an order |
| `GET` `getReservations(sku, locationId, from, to, pageable)` | `Page<Reservation>` | Paginated reservation history |

### Call Chains (Observed)

```
getInventoryState(sku, locationId)
  └─ InventoryService.getInventoryState(sku, locationId)
       └─ InventoryStateRepository.findBySkuAndLocationId(sku, locationId)
            → maps to InventoryStateResponse (lines 40-45)

getReconciliationSnapshot(sku, locationId)
  └─ InventoryService.getReconciliationSnapshot(sku, locationId)
       ├─ InventoryStateRepository.findBySkuAndLocationId (line 84)
       ├─ SafetyStockPolicyRepository.findActivePolicy(sku, locationId, now) (line 89)
       ├─ ReservedAggRepository.findBySkuAndLocationId (line 94)
       └─ ReconciliationSnapshotResponse.builder() (line 98)

getMovements(sku, locationId, from, to, pageable)
  └─ MovementService.getMovements(sku, locationId, from, to, pageable)
       ├─ IF from AND to present: MovementLedgerRepository.findAllBySkuAndLocationIdAndCreatedAtBetween (line 79)
       └─ ELSE: MovementLedgerRepository.findAllBySkuAndLocationId(sku, locationId, pageable) (line 82)

getDistinctSkus()
  └─ InventoryService.getDistinctSkus()
       └─ InventoryStateRepository.findDistinctSkus() or similar (inferred)

getReservationStatus(orderId)
  └─ ReservationService.getReservationStatus(orderId)
       └─ ReservationRepository.findByOrderId(orderId)
            → maps to ReservationStatusResponse (lines 235-241)

getReservations(sku, locationId, from, to, pageable)
  └─ ReservationService.getReservations(sku, locationId, from, to, pageable)
       ├─ IF from AND to present: ReservationRepository.findAllBySkuAndLocationIdAndCreatedAtBetween (line 249)
       └─ ELSE: ReservationRepository.findAllBySkuAndLocationId(sku, locationId, pageable) (line 252)
```

---

## 3b. Test Data Setup

### Inventory State Query
- `InventoryState(sku="SKU-001", locationId=1)` with `onHand=100, damaged=5, quarantine=3, inbound=10`

### Reconciliation Snapshot
- Same `InventoryState` as above
- `ReservedAgg(sku="SKU-001", locationId=1)` with `qtyReserved=20`
- Active `SafetyStockPolicy` with `minQty=10`

### Movement History
- Multiple `MovementLedger` rows for `sku="SKU-001"`, `locationId=1` with varying `createdAt` timestamps

### Reservation Status
- `Reservation(orderId="ORD-001", status=CONFIRMED, sku="SKU-001", locationId=1, qty=30)`

### Reservation List
- Multiple `Reservation` rows for `sku="SKU-001"`, `locationId=1`

---

## 3c. Happy Path Scenarios

### HP-1: Get Inventory State — All Buckets Populated
**Given**: `InventoryState(onHand=100, damaged=5, quarantine=3, inbound=10)`

**When**: `GET /inventory/state?sku=SKU-001&locationId=1`

**Then**:
- HTTP 200 OK
- `InventoryStateResponse { sku="SKU-001", locationId=1, onHand=100, damaged=5, quarantine=3, inbound=10 }`

### HP-2: Get Reconciliation Snapshot
**Given**: `onHand=100`, `qtyReserved=20`, `safetyStock=10` (active policy)

**When**: `GET /inventory/reconciliation?sku=SKU-001&locationId=1`

**Then**:
- HTTP 200 OK
- `ReconciliationSnapshotResponse { sku="SKU-001", locationId=1, onHand=100, qtyReserved=20, safetyStock=10 }`
- Assert all three data sources are joined correctly

### HP-3: Get Movements — Date Range Filter
**Given**: 10 movement records; 3 within range 2024-06-01 to 2024-06-30

**When**: `GET /inventory/movements?sku=SKU-001&locationId=1&from=2024-06-01T00:00:00Z&to=2024-06-30T23:59:59Z&page=0&size=10`

**Then**:
- HTTP 200 OK
- `Page<MovementLedger>` with 3 items
- `findAllBySkuAndLocationIdAndCreatedAtBetween` used (date range path)

### HP-4: Get Movements — No Date Range (All)
**Given**: 15 movement records for SKU-001 at location 1

**When**: `GET /inventory/movements?sku=SKU-001&locationId=1&page=0&size=10`

**Then**:
- HTTP 200 OK
- `Page<MovementLedger>` with 10 items (first page)
- `findAllBySkuAndLocationId` used

### HP-5: Get Distinct SKUs
**Given**: `InventoryState` rows for SKU-001, SKU-002, SKU-003

**When**: `GET /inventory/skus`

**Then**:
- HTTP 200 OK
- `["SKU-001", "SKU-002", "SKU-003"]`

### HP-6: Get Reservation Status
**Given**: `Reservation(orderId="ORD-001", status=CONFIRMED, qty=30)`

**When**: `GET /inventory/reservations/status?orderId=ORD-001`

**Then**:
- HTTP 200 OK
- `ReservationStatusResponse { id, orderId="ORD-001", sku, locationId, qty=30, status=CONFIRMED, createdAt }`

### HP-7: Get Reservations — With Date Range and Pagination
**Given**: 20 reservation records for SKU-001 at location 1, 5 within date range

**When**: `GET /inventory/reservations?sku=SKU-001&locationId=1&from=...&to=...&page=0&size=20`

**Then**:
- HTTP 200 OK
- `Page<Reservation>` with 5 items

---

## 3d. Error Path Scenarios

### EP-1 (Inventory State): SKU + Location Combination Not Found
**Given**: `InventoryStateRepository.findBySkuAndLocationId("GHOST-SKU", 1)` returns empty

**When**: `GET /inventory/state?sku=GHOST-SKU&locationId=1`

**Then**:
- [Hypothesized]: `NullPointerException` at `state.getSku()` call (line 40) if `Optional.get()` without check
- Or HTTP 404/400 if guarded with `orElseThrow`
- Assert: empty result handled gracefully

### EP-2 (Reconciliation): No InventoryState Row
**Given**: `findBySkuAndLocationId` returns empty at line 84

**When**: `GET /inventory/reconciliation?sku=GHOST-SKU&locationId=1`

**Then**:
- [Hypothesized]: NullPointerException or `IllegalArgumentException` → HTTP 500/400
- Snapshot should return zeros or 404

### EP-3 (Movements): No Movements in Date Range
**Given**: No `MovementLedger` rows in the specified date range

**When**: `GET /inventory/movements?sku=SKU-001&locationId=1&from=...&to=...`

**Then**:
- HTTP 200 OK — empty `Page<MovementLedger>` (no error)

### EP-4 (Movements): Invalid Date Format
**Given**: `from=notADate`

**When**: `GET /inventory/movements?sku=SKU-001&locationId=1&from=notADate`

**Then**:
- HTTP 400 — binding error for `OffsetDateTime`

### EP-5 (Reservation Status): Order Not Found
**Given**: `ReservationRepository.findByOrderId("ORD-NOTEXIST")` returns empty

**When**: `GET /inventory/reservations/status?orderId=ORD-NOTEXIST`

**Then**:
- [Inferred from `findByOrderId` at line 231 followed by field access at line 235]: `NullPointerException` or `IllegalArgumentException` → HTTP 400/500
- Assert: `Optional` is checked with `orElseThrow`

### EP-6 (Reservations): Missing Required Query Params
**Given**: `GET /inventory/reservations` without `sku` or `locationId`

**When**: Request sent

**Then**:
- [Hypothesized]: Spring MVC binding error → HTTP 400

---

## 3e. Edge Cases

### EC-1 (Inventory State): All Buckets Zero
**Given**: `InventoryState(onHand=0, damaged=0, quarantine=0, inbound=0)`

**When**: `GET /inventory/state`

**Then**:
- HTTP 200 OK — all zeros returned (valid state)

### EC-2 (Reconciliation): No Active Safety Stock Policy
**Given**: `findActivePolicy` returns empty

**When**: `GET /inventory/reconciliation`

**Then**:
- `safetyStock=0` in snapshot [Inferred — null/empty treated as 0]
- HTTP 200 OK

### EC-3 (Movements): Pagination — Last Page
**Given**: 25 movement records; page=2, size=10

**When**: `GET /inventory/movements?page=2&size=10`

**Then**:
- HTTP 200 OK — `Page` with 5 items, `hasNext=false`

### EC-4 (Movements): Date Range where `from` > `to`
**Given**: `from=2024-12-31`, `to=2024-01-01`

**When**: `GET /inventory/movements` with inverted range

**Then**:
- [Hypothesized]: Empty page returned (no records match inverted range) or HTTP 400 if validated

### EC-5 (Distinct SKUs): Very Large Number of SKUs
**Given**: 100,000 distinct SKUs in `InventoryState`

**When**: `GET /inventory/skus`

**Then**:
- [Hypothesized]: Memory pressure; may need pagination
- Assert: response time and memory within acceptable bounds

### EC-6 (Reconciliation): Snapshot Consistency
**Given**: Concurrent movement being recorded while snapshot is queried

**When**: `GET /inventory/reconciliation` during active `recordMovement` transaction

**Then**:
- [Hypothesized]: Snapshot may be momentarily inconsistent if reads are not in same transaction
- `onHand - qtyReserved - safetyStock` may not match ATP from `AvailabilityService` at exact same moment
- Assert eventual consistency after operations complete

---

## 3f. Mock Boundaries

### Inventory State
| Dependency | Method | Happy Path Stub | Error Path Stub |
|---|---|---|---|
| `InventoryStateRepository` | `findBySkuAndLocationId(String, Long)` | Return `Optional.of(state)` | Return `Optional.empty()` |

### Reconciliation Snapshot
| Dependency | Method | Happy Path Stub | Error Path Stub |
|---|---|---|---|
| `InventoryStateRepository` | `findBySkuAndLocationId(String, Long)` | Return state | Return empty |
| `SafetyStockPolicyRepository` | `findActivePolicy(String, Long, OffsetDateTime)` | Return policy | Return empty |
| `ReservedAggRepository` | `findBySkuAndLocationId(String, Long)` | Return agg | Return empty |

### Movements
| Dependency | Method | Happy Path Stub | Error Path Stub |
|---|---|---|---|
| `MovementLedgerRepository` | `findAllBySkuAndLocationIdAndCreatedAtBetween(...)` | Return `Page` with records | Return empty `Page` |
| `MovementLedgerRepository` | `findAllBySkuAndLocationId(String, Long, Pageable)` | Return `Page` with records | Return empty `Page` |

### Reservation Status
| Dependency | Method | Happy Path Stub | Error Path Stub |
|---|---|---|---|
| `ReservationRepository` | `findByOrderId(String)` | Return `Optional.of(reservation)` | Return `Optional.empty()` |

### Reservations List
| Dependency | Method | Happy Path Stub | Error Path Stub |
|---|---|---|---|
| `ReservationRepository` | `findAllBySkuAndLocationIdAndCreatedAtBetween(...)` | Return `Page` | Return empty `Page` |
| `ReservationRepository` | `findAllBySkuAndLocationId(String, Long, Pageable)` | Return `Page` | Return empty `Page` |
