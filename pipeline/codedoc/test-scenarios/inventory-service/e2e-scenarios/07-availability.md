---
title: "E2E Scenarios — Check Inventory Availability"
type: "e2e-test-scenario"
flow: "inventory-availability"
entry_point: "com.inventory.inventoryservice.controller.AvailabilityController.getAvailability(String,Integer) | getBulkAvailability(BulkAvailabilityRequest)"
evidence: "Observed"
timestamp: "2025-01-24T00:00:00Z"
---

# Flow: Check Inventory Availability

## 3a. Flow Summary [Observed]

Two endpoints are covered: single-SKU availability and bulk availability.

### Single Availability
| Attribute | Value |
|---|---|
| **Entry Point** | `GET /availability?sku=...&locationId=...` (inferred) |
| **Controller Method** | `AvailabilityController.getAvailability(String sku, Integer locationId)` — line 19 |
| **Returns** | `ResponseEntity<AvailabilityResponse>` |
| **Description** | Returns the Available-to-Promise (ATP) quantity for a single SKU at a single location: `ATP = onHand − reserved − safetyStock`. |

### Bulk Availability
| Attribute | Value |
|---|---|
| **Entry Point** | `POST /availability/bulk` (inferred) |
| **Controller Method** | `AvailabilityController.getBulkAvailability(BulkAvailabilityRequest)` — line 24 |
| **Returns** | `ResponseEntity<List<AvailabilityResponse>>` |
| **Description** | Returns ATP for a list of SKUs at a single location. Internally iterates and calls `getAvailability` per SKU. |

### Shared Call Chain (Observed)
```
getAvailability(sku, locationId)
  └─ AvailabilityService.getAvailability(sku, locationId)
       ├─ getOnHand(sku, locationId)   → InventoryStateRepository.findBySkuAndLocationId (inferred)
       ├─ getReserved(sku, locationId) → ReservedAggRepository.findBySkuAndLocationId    (inferred)
       ├─ getSafetyStock(sku, locationId) → SafetyStockPolicyRepository.findActivePolicy (inferred)
       └─ AvailabilityResponse.builder().sku().locationId().atp(onHand - reserved - safetyStock) (line 33)

getBulkAvailability(skus, locationId)
  └─ AvailabilityService.getBulkAvailability(List<String> skus, Integer locationId)
       └─ [for each sku] → AvailabilityService.getAvailability(sku, locationId)  (line 42)
```

---

## 3b. Test Data Setup

### Single Availability — Required DB State
- `InventoryState(sku="SKU-001", locationId=1)` with `onHand=100`
- `ReservedAgg(sku="SKU-001", locationId=1)` with `qtyReserved=20`
- Active `SafetyStockPolicy(sku="SKU-001", locationId=1)` with `minQty=10` and overlapping effective dates

### Single Availability — Query Parameters
```
GET /availability?sku=SKU-001&locationId=1
```

### Bulk Availability — Input Payload — `BulkAvailabilityRequest`
```json
{
  "skus": ["SKU-001", "SKU-002", "SKU-003"],
  "locationId": 1
}
```

### Field Types [Observed]
| Field (Single) | Type |
|---|---|
| `sku` | `String` (query param) |
| `locationId` | `Integer` (query param) |

| Field (Bulk) | Type |
|---|---|
| `skus` | `List<String>` |
| `locationId` | `Integer` |

### Response Structure — `AvailabilityResponse` [Observed]
```json
{
  "sku": "SKU-001",
  "locationId": 1,
  "atp": 70
}
```
ATP = 100 (onHand) − 20 (reserved) − 10 (safetyStock) = 70

---

## 3c. Happy Path Scenarios

### HP-1: Single SKU Availability — Full Data Present
**Given**:
- `onHand=100`, `qtyReserved=20`, `safetyStock=10`

**When**: `GET /availability?sku=SKU-001&locationId=1`

**Then**:
- HTTP 200 OK
- `AvailabilityResponse { sku="SKU-001", locationId=1, atp=70 }`

### HP-2: Single SKU — No Reservations (ReservedAgg absent)
**Given**:
- `onHand=50`, no `ReservedAgg` row (qtyReserved treated as 0)
- Active safety stock: `minQty=5`

**When**: `GET /availability?sku=SKU-001&locationId=1`

**Then**:
- HTTP 200 OK
- `atp = 50 - 0 - 5 = 45`

### HP-3: Single SKU — No Safety Stock Policy
**Given**:
- `onHand=80`, `qtyReserved=10`
- No active `SafetyStockPolicy` (safetyStock treated as 0)

**When**: `GET /availability?sku=SKU-001&locationId=1`

**Then**:
- HTTP 200 OK
- `atp = 80 - 10 - 0 = 70`

### HP-4: Single SKU — ATP is Zero
**Given**:
- `onHand=30`, `qtyReserved=20`, `safetyStock=10`

**When**: `GET /availability?sku=SKU-001&locationId=1`

**Then**:
- HTTP 200 OK
- `atp = 0`

### HP-5: Bulk Availability — Multiple SKUs
**Given**:
- `SKU-001`: onHand=100, reserved=20, safetyStock=10 → atp=70
- `SKU-002`: onHand=50, reserved=0, safetyStock=5 → atp=45
- `SKU-003`: onHand=0, reserved=0, safetyStock=0 → atp=0

**When**: `POST /availability/bulk` with skus=["SKU-001","SKU-002","SKU-003"], locationId=1

**Then**:
- HTTP 200 OK
- Response: list of 3 `AvailabilityResponse` objects with correct ATP values

### HP-6: Bulk Availability — Single Item List
**Given**: `skus=["SKU-001"]`

**When**: `POST /availability/bulk`

**Then**:
- HTTP 200 OK
- Response: list with one element

---

## 3d. Error Path Scenarios

### EP-1 (Single): Missing `sku` Query Parameter
**Given**: `GET /availability?locationId=1` (no sku)

**When**: Request sent

**Then**:
- HTTP 400 — Spring MVC binding failure [Inferred — `@RequestParam` without `required=false`]
- Or `handleGeneral` catches binding error → HTTP 500 [Hypothesized if not annotated with `@RequestParam(required=true)`]

### EP-2 (Single): Missing `locationId` Query Parameter
**Given**: `GET /availability?sku=SKU-001` (no locationId)

**When**: Request sent

**Then**:
- HTTP 400 — binding error [Inferred]

### EP-3 (Single): SKU Not Found / No InventoryState
**Given**: `InventoryStateRepository.findBySkuAndLocationId("GHOST-SKU", 1)` returns empty

**When**: `GET /availability?sku=GHOST-SKU&locationId=1`

**Then**:
- [Hypothesized]: onHand = 0, atp = 0 − 0 − safetyStock → could go negative if safetyStock > 0
- Or HTTP 404 / 400 if service throws exception for unknown SKU
- Flag for review: define behaviour for unknown SKUs (zero vs error)

### EP-4 (Bulk): Empty `skus` List
**Given**: `{ "skus": [], "locationId": 1 }`

**When**: `POST /availability/bulk`

**Then**:
- [Hypothesized]: HTTP 200 with empty list, or HTTP 400 if `@Size(min=1)` validation exists

### EP-5 (Bulk): `skus` List with Null Entry
**Given**: `{ "skus": [null, "SKU-001"], "locationId": 1 }`

**When**: `POST /availability/bulk`

**Then**:
- [Hypothesized]: HTTP 400 validation error or `NullPointerException` → HTTP 500

### EP-6 (Bulk): Very Large SKU List (performance/payload limit)
**Given**: `skus` list with 1000 entries

**When**: `POST /availability/bulk`

**Then**:
- [Hypothesized]: May hit `PayloadTooLargeException` if body exceeds `maxBytes`
- Or performance degradation — N×1 DB queries (no batch observed)

### EP-7: Invalid `locationId` Type
**Given**: `GET /availability?sku=SKU-001&locationId=notAnInteger`

**When**: Request sent

**Then**:
- HTTP 400 — `HttpMessageNotReadableException` or binding error via `handleMalformedJson`

---

## 3e. Edge Cases

### EC-1: ATP Would Be Negative
**Given**: `onHand=5`, `qtyReserved=10`, `safetyStock=0` → computed atp = -5

**When**: `GET /availability?sku=SKU-001&locationId=1`

**Then**:
- [Hypothesized]: atp returned as -5, or clamped to 0 with `Math.max(0, computed)`
- Assert expected behaviour — negative ATP is a signal of data inconsistency

### EC-2: Safety Stock Policy Boundary (effectiveTo = now)
**Given**: `SafetyStockPolicy.effectiveTo` equals the exact moment of the query

**When**: `GET /availability`

**Then**:
- `findActivePolicy` uses `<=` comparison — policy may or may not be included depending on query predicate
- Test both `effectiveTo=now` and `effectiveTo=now-1second`

### EC-3: Bulk — Duplicate SKUs in Request
**Given**: `{ "skus": ["SKU-001", "SKU-001", "SKU-001"], "locationId": 1 }`

**When**: `POST /availability/bulk`

**Then**:
- [Inferred]: Three identical `AvailabilityResponse` entries returned (iterates list without deduplication at line 42)
- Or deduplicated if upstream validation exists
- Verify expected behaviour

### EC-4: Bulk — All SKUs Have Zero Stock
**Given**: All requested SKUs have `onHand=0`

**When**: `POST /availability/bulk`

**Then**:
- HTTP 200 OK
- All `atp=0` (or negative if safety stock > 0)

---

## 3f. Mock Boundaries

| Dependency | Method | Happy Path Stub | Error Path Stub |
|---|---|---|---|
| `InventoryStateRepository` | `findBySkuAndLocationId(String, Long)` | Return state with `onHand=100` | Return `Optional.empty()` |
| `ReservedAggRepository` | `findBySkuAndLocationId(String, Long)` | Return agg with `qtyReserved=20` | Return `Optional.empty()` (0 reserved) |
| `SafetyStockPolicyRepository` | `findActivePolicy(String, Long, OffsetDateTime)` | Return policy with `minQty=10` | Return `Optional.empty()` (0 safety stock) |

### Isolation Notes
- `AvailabilityService` has no `@Transactional` — read-only operations; no rollback concerns
- Mock all three repositories independently to test each component of the ATP formula
- Integration test: seed DB with known values, assert ATP calculation end-to-end
