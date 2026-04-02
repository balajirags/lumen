# Domain Analysis — Capabilities & Bounded Contexts

## Business Capabilities [Inferred]

### Capability 1: Inventory State Management
**Core Operations:** Query current stock levels by SKU and location; track inventory in four states (on-hand, inbound, damaged, quarantine).

**Key Invariants:**
- Each SKU/location combo has exactly one `InventoryState` entity (unique composite key: locId + sku)
- Inventory counts must always be non-negative
- Damaged and quarantine quantities are subtractions from usable inventory
- Updates must be consistent with movements (no unrecorded movements)

**Domain Entity:** `InventoryState` (fields: onHand, inbound, damaged, quarantine, locationId, sku, updatedAt)

---

### Capability 2: Movement & Ledger
**Core Operations:** Record all stock movements between locations; create permanent audit trail.

**Key Invariants:**
- Every movement must have a reference ID for traceability
- Movement type defines source/destination behavior (MUL (MULTI), etc.)
- Ledger entries are immutable; only add new entries, never modify existing
- Movement quantity must be positive integers

**Domain Entities:** `MovementLedger` (fields: qty, type, locationId, sku, source, referenceId, createdAt)

---

### Capability 3: Reservation Management
**Core Operations:** Create temporal reservations (soft-holds) for orders that may or may not be confirmed.

**Key Invariants:**
- Soft-held inventory must be tracked in `ReservedAgg` to prevent overselling
- Reservations have lifecycle: `SOFT_HELD` → `CONFIRMED` / `CANCELLED` / `RELEASED`
- Soft-holds expire after configurable TTL (TEMP_RESERVATION_TTL_MINUTES)
- Only one reservation per order/SKU/location combo can exist without confirmation

**Domain Entities:** `Reservation` (fields: status, orderId, qty, locationId, sku, createdAt), `ReservedAgg` (aggregation: qtyReserved)

---

### Capability 4: Availability (ATP) Calculation
**Core Operations:** Compute Available-to-Promise (ATP) for new orders based on current state.

**Key Invariants:**
- ATP = onHand + inbound - damaged - quarantine - qtyReserved - minQty(safety stock)
- Must account for all conflicting reservations across location/SKU
- Safety stock minimum must be respected (ATP can be negative if safety stock is required)
- Calculations must be idempotent and consistent

**Domain View:** Aggregates from `InventoryState`, `ReservedAgg`, `SafetyStockPolicy`

---

### Capability 5: Safety Stock Policy Management
**Core Operations:** Define and enforce minimum stock levels per location/SKU.

**Key Invariants:**
- Policies are time-bound (effectiveFrom, effectiveTo)
- Must have unique effective range per location/SKU (no overlapping periods)
- Safety stock affects ATP calculations and reorder point triggers
- Policies drive business rules for stock replenishment

**Domain Entity:** `SafetyStockPolicy` (fields: minQty, ruleType, locationId, sku, effectiveFrom, effectiveTo, updatedAt)

---

### Capability 6: Location Management
**Core Operations:** Manage physical locations (warehouses, stores) with full address and type information.

**Key Invariants:**
- Each location has exactly one record with unique ID
- Location types determine operational characteristics (warehouse, store, distribution center)
- Active/inactive status controls whether location can participate in inventory operations
- Geospatial data (lat/long) enables routing optimization

**Domain Entity:** `Location` (fields: name, locationType, address, coordinates, isActive)

---

### Capability 7: Reservation Expiry
**Core Operations:** Background processing to auto-cancel soft-holds that exceed TTL.

**Key Invariants:**
- Reservations must be automatically cancelled if not confirmed within TTL
- Uses Redis ephemeral key expiry for efficient polling-free detection
- Must coordinate cancellation across all downstream systems (Kafka events)
- Expiration handling must be idempotent (same event can fire multiple times safely)

**Domain View:** Implements soft-hold TTL policy via event-driven architecture

---

## Bounded Context Candidates [Inferred]

Based on cohesive domain analysis and entity aggregation patterns, here are the logical bounded contexts:

| Context | Aggregate Root | Key Entities | Upstream Dependencies | Downstream Dependencies |
|---------|----------------|--------------|----------------------|------------------------|
| **Inventory Context** | `InventoryState` | InventoryState, MovementLedger | ReservationContext, SafetyStockContext | AvailabilityContext |
| **Reservation Context** | `Reservation` | Reservation, ReservedAgg | InventoryContext, SafetyStockContext | (Publishes events only) |
| **Safety Stock Context** | `SafetyStockPolicy` | SafetyStockPolicy, SafetyStockPolicyId | (Independent) | InventoryContext, AvailabilityContext |
| **Location Context** | `Location` | Location | (Independent) | InventoryContext, ReservationContext |
| **Availability Context** | View | *Computed* (ATP calculation) | InventoryContext, ReservationContext, SafetyStockContext | External ordering systems |
| **Movement Context** | `MovementLedger` | MovementLedger | LocationContext, SafetyStockContext (validation) | (Publishes events only) |

**Rationale:**
- **Inventory Context** owns stock state and movements — core aggregation responsibility
- **Reservation Context** is domain-rich with complex lifecycle; should be separated to avoid inventory service complexity
- **Safety Stock Context** has independent lifecycle (time-bounded policies)
- **Availability Context** is a derived capability (read-only view) — could be a separate read model
- **Movement** and **Reservation** publish events but are less independent than Reservation

**Boundary Recommendations:**
- `MovementService` should belong to InventoryContext (writes to both InventoryState and MovementLedger)
- `ReservationService` should be extracted to ReservationContext (complex lifecycle, owns ReservedAgg)
- `AvailabilityService` could move to AvailabilityContext (read-heavy, aggregation logic)

---

## Domain Events [Observed]

Based on `KafkaProducerService` topic definitions, here are the domain events:

| Event Topic | Published By | Event Type | Consumers / Usage |
|-------------|--------------|------------|-------------------|
| `MOVEMENT_RECORDED` | `MovementService` | Stock movement completed | External analytics, reporting systems |
| `RESERVATION_SOFT_HELD` | `ReservationService` | Temporary reservation created | Order confirmation services, downstream fulfillment |
| `RESERVATION_CONFIRMED` | `ReservationService` | Reservation finalized (order confirmed) | Inventory reservation, shipment preparation |
| `RESERVATION_CANCELLED` | `ReservationService` (or listener) | Reservation cancelled (order cancelled/user action) | Inventory release, re-allocation |
| `RESERVATION_RELEASED` | `ReservationService` | Reservation released (pre-confirmation release) | Inventory becomes available |
| `RESERVATION_SOFT_HOLD_RELEASED` | `TempReservationExpiryListener` | Temp hold expired and released | Inventory restoration, customer notification |
| `SAFETY_STOCK_POLICY_UPDATED` | `SafetyStockService` | New/updated safety stock policy | Reorder point calculators, procurement systems |

**Event Contract Pattern:**
All events follow consistent structure with domain entity data embedded in message payloads. Consumer dependencies are inferred from service ownership but not explicitly verified in graph.

---

## Capability Maturity [Hypothesized]

| Capability | Maturity | Assessment Rationale |
|------------|----------|----------------------|
| **Inventory State Management** | Established | Fully implemented with comprehensive CRUD; entity aggregation is robust; all inventory operations flow through this service |
| **Movement & Ledger** | Established | Permanent audit trail with immutability guarantees; integrates with inventory state updates; event-driven publishing |
| **Reservation Management** | Developing | Soft-hold mechanism exists but may lack full lifecycle (missing explicit confirmation/cancellation flows in DTOs); Redis TTL handling is present |
| **Availability (ATP) Calculation** | Established | Complex aggregation logic present; combines multiple domain entities; used by inventory service orchestration |
| **Safety Stock Policy Management** | Established | Time-bound policy enforcement with temporal queries; policy updates published as events |
| **Location Management** | Established | Complete location CRUD with geographic data; active/inactive status controls business rules |
| **Reservation Expiry** | Developing | Event-driven expiry using Redis key expiry; appears functional but may lack comprehensive edge-case handling |

**Gap Analysis:**
- Reservation confirmation/release flows are present in DTOs but may not have full service-level orchestration
- No explicit reorder point or procurement triggers visible (safety stock policies exist but no downstream integration)
- No reconciliation or auditing capability visible beyond movement ledger
- No multi-party reservation support seen (single-order reservations only)

---

## Domain Model Summary

**Total Domain Entities:** 6 primary entities + 3 supporting aggregates

1. **`InventoryState`** — stock levels by SKU/location (composite key)
2. **`MovementLedger`** — immutable movement history
3. **`Reservation`** — temporal order holds
4. **`ReservedAgg`** — aggregation for reserved quantities (composite key)
5. **`SafetyStockPolicy`** — time-bound minimum stock rules
6. **`Location`** — physical warehouse/store references

**Domain Events:** 7 Kafka topics supporting event-driven architecture

_(Entities: see current-state/inventory.md)_