---
title: "E2E Scenarios — Manage Safety Stock Policy"
type: "e2e-test-scenario"
flow: "safety-stock-policy"
entry_point: "com.inventory.inventoryservice.controller.SafetyStockController.createSafetyStockPolicy | updateSafetyStockPolicy | getSafetyStockPolicy"
evidence: "Observed"
timestamp: "2025-01-24T00:00:00Z"
---

# Flow: Manage Safety Stock Policy

## 3a. Flow Summary [Observed]

Safety stock policies define the minimum quantity of a SKU that must remain available (not allocated) at a location for a given time window.

### Create Safety Stock Policy
| Attribute | Value |
|---|---|
| **Entry Point** | `POST /safety-stock` (inferred) |
| **Controller** | `SafetyStockController.createSafetyStockPolicy(SafetyStockPolicyRequest)` — line 23 |
| **Returns** | `ResponseEntity<SafetyStockPolicy>` |
| **Transaction** | None observed on service method |
| **Kafka Event** | `SAFETY_STOCK_POLICY_UPDATED_TOPIC` |

### Update Safety Stock Policy
| Attribute | Value |
|---|---|
| **Entry Point** | `PUT /safety-stock/{sku}/{locationId}/{effectiveFrom}` (inferred) |
| **Controller** | `SafetyStockController.updateSafetyStockPolicy(String sku, Long locationId, OffsetDateTime effectiveFrom, SafetyStockPolicyRequest body)` — line 29 |
| **Returns** | `ResponseEntity<SafetyStockPolicy>` |
| **Transaction** | None observed on service method |
| **Kafka Event** | `SAFETY_STOCK_POLICY_UPDATED_TOPIC` |

### Get Safety Stock Policy
| Attribute | Value |
|---|---|
| **Entry Point** | `GET /safety-stock/{sku}/{locationId}?asOf=...` (inferred) |
| **Controller** | `SafetyStockController.getSafetyStockPolicy(String sku, Long locationId, Optional<OffsetDateTime> asOf)` — line 41 |
| **Returns** | `ResponseEntity<SafetyStockPolicyResponse>` |
| **Transaction** | None observed |
| **Kafka Event** | None |

### Call Chains (Observed)
```
createSafetyStockPolicy(request)
  └─ SafetyStockService.createSafetyStockPolicy(request)
       ├─ SafetyStockPolicy entity populated (lines 30-37): sku, locationId, minQty, ruleType, effectiveFrom, effectiveTo
       ├─ SafetyStockPolicyRepository.save(policy)  (inferred from line 38)
       └─ KafkaProducerService.sendSafetyStockPolicyUpdatedEvent(policy) (line 39)

updateSafetyStockPolicy(sku, locationId, effectiveFrom, request)
  └─ SafetyStockService.updateSafetyStockPolicy(sku, locationId, effectiveFrom, request)
       ├─ SafetyStockPolicyId key constructed (lines 45-47)
       ├─ SafetyStockPolicyRepository.findById(key)  (line 49-50, inferred from EXPRESSION at line 49)
       ├─ Policy fields updated: minQty, ruleType, effectiveTo  (lines 53-55)
       ├─ SafetyStockPolicyRepository.save(policy)  (inferred, line 58)
       └─ KafkaProducerService.sendSafetyStockPolicyUpdatedEvent(policy) (line 59)

getSafetyStockPolicy(sku, locationId, asOf)
  └─ SafetyStockService.getSafetyStockPolicy(sku, locationId, Optional<OffsetDateTime>)
       └─ SafetyStockPolicyRepository.findBySkuAndLocationIdAndEffectiveFromLessThanEqualAndEffectiveToGreaterThanEqual(sku, locationId, asOf, asOf) (line 66)

(mapping to SafetyStockPolicyResponse happens in controller, lines 48-53)
```

---

## 3b. Test Data Setup

### Create — Input Payload — `SafetyStockPolicyRequest`
```json
{
  "sku": "SKU-001",
  "locationId": 1,
  "minQty": 10,
  "ruleType": "MINIMUM",
  "effectiveFrom": "2024-01-01T00:00:00Z",
  "effectiveTo": "2024-12-31T23:59:59Z"
}
```

### Update — Path Parameters + Body
```
PUT /safety-stock/SKU-001/1/2024-01-01T00:00:00Z
```
Body:
```json
{
  "minQty": 20,
  "ruleType": "MINIMUM",
  "effectiveTo": "2025-06-30T23:59:59Z"
}
```

### Get — Query Parameters
```
GET /safety-stock/SKU-001/1?asOf=2024-06-15T00:00:00Z
```

### Field Types [Observed]
| Field | Type | Notes |
|---|---|---|
| `sku` | `String` | Required |
| `locationId` | `Long` | Required |
| `minQty` | `Integer` | Required, minimum quantity |
| `ruleType` | `String` | Required, e.g. "MINIMUM" |
| `effectiveFrom` | `OffsetDateTime` | Required — policy start date |
| `effectiveTo` | `OffsetDateTime` | Required — policy end date |

### Composite Primary Key [Observed]
`SafetyStockPolicyId` = `(sku, locationId, effectiveFrom)` — uniquely identifies a policy version

---

## 3c. Happy Path Scenarios

### HP-1 (Create): Create New Policy
**Given**: No existing policy for `(sku="SKU-001", locationId=1, effectiveFrom=2024-01-01)`

**When**: `POST /safety-stock` with valid payload

**Then**:
- HTTP 200/201 OK (inferred)
- Response body: `SafetyStockPolicy` entity with all fields populated
- `SafetyStockPolicy` persisted in DB
- Kafka event published to `SAFETY_STOCK_POLICY_UPDATED_TOPIC`

### HP-2 (Create): Create Policy with Same SKU/Location but Different Period
**Given**: Policy already exists for `effectiveFrom=2024-01-01`; creating policy for `effectiveFrom=2025-01-01`

**When**: `POST /safety-stock` with `effectiveFrom=2025-01-01`

**Then**:
- HTTP 200/201 OK — new policy version created (different PK)
- Two active policies exist for different time windows

### HP-3 (Update): Update minQty and effectiveTo
**Given**:
- Policy exists for `(sku="SKU-001", locationId=1, effectiveFrom=2024-01-01)`
- Current `minQty=10`

**When**: `PUT /safety-stock/SKU-001/1/2024-01-01T00:00:00Z` with `minQty=20`

**Then**:
- HTTP 200 OK
- Response body: updated `SafetyStockPolicy` with `minQty=20`
- DB record updated (only `minQty`, `ruleType`, `effectiveTo` modified — key fields unchanged)
- Kafka event published

### HP-4 (Get): Get Active Policy at a Specific Date
**Given**:
- Policy: `effectiveFrom=2024-01-01`, `effectiveTo=2024-12-31`
- Query: `asOf=2024-06-15`

**When**: `GET /safety-stock/SKU-001/1?asOf=2024-06-15T00:00:00Z`

**Then**:
- HTTP 200 OK
- `SafetyStockPolicyResponse` with all fields mapped [Observed: controller maps fields at lines 48-53]

### HP-5 (Get): Get Policy without `asOf` (defaults to current time)
**Given**: Policy is active as of now

**When**: `GET /safety-stock/SKU-001/1` (no `asOf` param — `Optional<OffsetDateTime>` → empty)

**Then**:
- HTTP 200 OK
- Policy active at current timestamp returned

---

## 3d. Error Path Scenarios

### EP-1 (Update): Policy Not Found for Given Key
**Given**: `SafetyStockPolicyRepository.findById(key)` returns empty for the given (sku, locationId, effectiveFrom)

**When**: `PUT /safety-stock/SKU-999/99/2024-01-01T00:00:00Z`

**Then**:
- [Hypothesized from `findById` followed by `.orElseThrow()` or null check]: HTTP 400 via `handleIllegalArgument` or HTTP 404
- Body: `ErrorResponse { message: "Safety stock policy not found …" }`

### EP-2 (Create): Duplicate Policy (Same PK)
**Given**: Policy with `(sku="SKU-001", locationId=1, effectiveFrom=2024-01-01)` already exists

**When**: `POST /safety-stock` with same `effectiveFrom`

**Then**:
- [Hypothesized]: `DataIntegrityViolationException` (PK violation) → HTTP 500 via `handleGeneral`, or upsert behaviour

### EP-3 (Create/Update): Missing Required Fields
**Given**: `SafetyStockPolicyRequest` missing `sku` or `effectiveFrom`

**When**: POST or PUT request

**Then**:
- HTTP 400 — `handleValidation`

### EP-4 (Create/Update): `effectiveTo` before `effectiveFrom`
**Given**: `effectiveTo=2023-01-01` and `effectiveFrom=2024-01-01` (effectiveTo in the past relative to effectiveFrom)

**When**: `POST /safety-stock`

**Then**:
- [Hypothesized]: No `@AssertTrue` validation observed — may succeed in DB but cause incorrect policy lookups
- Flag for business rule validation to be added

### EP-5 (Get): No Active Policy for Date
**Given**: Query `asOf=2030-01-01`, no policy covers that date

**When**: `GET /safety-stock/SKU-001/1?asOf=2030-01-01T00:00:00Z`

**Then**:
- [Hypothesized]: `NullPointerException` if controller calls `.get()` on empty Optional at line 48
- Or returns empty `SafetyStockPolicyResponse` — verify mapping handles Optional.empty()

### EP-6 (Create/Update): Kafka Failure
**Given**: Kafka unavailable during `sendSafetyStockPolicyUpdatedEvent`

**When**: POST or PUT request

**Then**:
- [Hypothesized]: Exception propagates → HTTP 500
- DB write may have committed if no `@Transactional` wraps the service method — verify

---

## 3e. Edge Cases

### EC-1: Overlapping Policy Periods
**Given**: Two policies for same SKU × location with overlapping effective dates

**When**: `POST /safety-stock` creating overlapping policy

**Then**:
- [Hypothesized]: Both inserted (no overlap check observed); `findActivePolicy` may return multiple → data anomaly
- Assert: business rule or DB unique constraint prevents overlaps

### EC-2: `minQty=0` (Zero Safety Stock)
**Given**: `minQty=0`

**When**: `POST /safety-stock`

**Then**:
- HTTP 200 OK (valid zero-safety-stock policy)
- ATP calculation: `onHand - reserved - 0` = full on-hand minus reserved
- Assert: `@Min(0)` validation allows zero, `@Min(1)` would reject

### EC-3: Very High `minQty`
**Given**: `minQty=999999`

**When**: `POST /safety-stock`

**Then**:
- Policy created
- All reservation requests for this SKU × location will fail (insufficient ATP)
- Test interplay with availability calculation

### EC-4: `effectiveFrom` in the Past
**Given**: `effectiveFrom=2000-01-01T00:00:00Z` (24 years ago)

**When**: `POST /safety-stock`

**Then**:
- [Hypothesized]: Allowed (historical policies acceptable)
- Verify no business rule prevents backdating

### EC-5: `ruleType` Not in Allowed Set
**Given**: `ruleType="UNKNOWN_RULE"`

**When**: `POST /safety-stock`

**Then**:
- [Hypothesized]: Stored as-is (String type, no enum validation observed)
- Or `handleIllegalArgument` if service validates

---

## 3f. Mock Boundaries

### Create
| Dependency | Method | Happy Path Stub | Error Path Stub |
|---|---|---|---|
| `SafetyStockPolicyRepository` | `save(SafetyStockPolicy)` | Return persisted policy | Throw `DataIntegrityViolationException` (PK collision) |
| `KafkaProducerService` | `sendSafetyStockPolicyUpdatedEvent(SafetyStockPolicy)` | No-op | Throw `KafkaException` |

### Update
| Dependency | Method | Happy Path Stub | Error Path Stub |
|---|---|---|---|
| `SafetyStockPolicyRepository` | `findById(SafetyStockPolicyId)` | Return `Optional.of(existingPolicy)` | Return `Optional.empty()` |
| `SafetyStockPolicyRepository` | `save(SafetyStockPolicy)` | Return updated policy | Throw `DataIntegrityViolationException` |
| `KafkaProducerService` | `sendSafetyStockPolicyUpdatedEvent(SafetyStockPolicy)` | No-op | Throw `KafkaException` |

### Get
| Dependency | Method | Happy Path Stub | Error Path Stub |
|---|---|---|---|
| `SafetyStockPolicyRepository` | `findBySkuAndLocationIdAndEffectiveFromLessThanEqualAndEffectiveToGreaterThanEqual(...)` | Return `Optional.of(policy)` | Return `Optional.empty()` |
