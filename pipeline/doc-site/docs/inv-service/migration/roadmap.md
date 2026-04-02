# Migration Roadmap — Risk Analysis & Phased Modernization

## Risk Matrix [Observed]

| Component | Risk Type | Coupling Score | Estimated Migration Impact |
|-----------|-----------|----------------|---------------------------|
| LocationService.updateLocation | High coupling, complex orchestration | 23 | HIGH - Touches location CRUD, cascades to inventory states |
| ReservationService.confirmReservation | Complex state machine, transactional | 20 | HIGH - Manages reservation lifecycle, affects ATP calculations |
| SafetyStockService.createSafetyStockPolicy | Configuration-intensive, external events | 17 | MEDIUM - Policy engine with Kafka notifications |
| KafkaProducerService | Central messaging hub, multiple topics | 15 | HIGH - All async notifications flow through this |
| MovementService.executeTransfer | Cross-location inventory changes | 9 | MEDIUM - Two-phase inventory updates |
| LocationService (class-level) | God class smell (37 members) | N/A | MEDIUM - Large class suggests consolidation opportunities |
| InventoryService | Core orchestration, multiple dependencies | 14 | HIGH - Central business logic hub |

### Risk Severity Definitions
- **HIGH**: Requires dedicated migration effort, potential for regression, multiple affected services
- **MEDIUM**: Well-understood patterns, isolated changes, minimal cross-cutting concerns
- **LOW**: Simple refactor, local impact only

---

## Dead Code Candidates [Observed]

### Uncalled Public Methods (40 identified)
1. `PasswordGeneratorService.addCorsMappings(CorsRegistry)` - CORS configuration, unused
2. `InventoryServiceApplication.corsConfigurer()` - CORS configuration method
3. `PasswordController.generate(Integer)` - Password generation endpoint (possibly legacy)
4. `AvailabilityController.getAvailability(String,Integer)` - Individual availability lookup
5. `AvailabilityController.getBulkAvailability(BulkAvailabilityRequest)` - Bulk availability query
6. `EncodeController.encodeBase64(EncodeRequest)` - Base64 encoding utility
7. `InventoryController.cancelReservation(ReservationCancelRequest)` - May be covered by other cancellation flows
8. `InventoryController.confirmReservation(ReservationConfirmRequest)` - May be redundant with service layer
9. `InventoryController.createReservation(ReservationCreateRequest)` - May be covered by other flows
10. `InventoryController.getDistinctSkus()` - SKU discovery endpoint
11. `InventoryController.getInventoryState(String,Long)` - Direct state query
12. `InventoryController.getMovements(...)` - Movement history query
13. `InventoryController.getReconciliationSnapshot(String,Long)` - Audit/snapshot query
14. `InventoryController.getReservationStatus(String)` - Reservation status query
15. `InventoryController.getReservations(...)` - Reservation list query
16. `InventoryController.recordMovement(MovementRequest)` - Movement recording
17. `InventoryController.releaseReservation(ReservationReleaseRequest)` - Reservation release
18. `InventoryController.ship(ShipmentRequest)` - Shipment processing
19. `InventoryController.transferInventory(TransferRequest)` - Inventory transfer
20. `LocationController.createLocation(Location)` - Location creation
21. `LocationController.getAllLocations()` - Location listing
22. `LocationController.updateLocation(Long,Location)` - Location update
23. `SafetyStockController.createSafetyStockPolicy(...)` - Policy creation
24. `SafetyStockController.getSafetyStockPolicy(...)` - Policy retrieval
25. `SafetyStockController.updateSafetyStockPolicy(...)` - Policy update
26. `GlobalExceptionHandler.handleGeneral(Exception)` - General exception handler
27. `GlobalExceptionHandler.handleIllegalArgument(IllegalArgumentException)` - Validation error handler
28. `GlobalExceptionHandler.handleMalformedJson(HttpMessageNotReadableException)` - JSON parsing error handler
29. `GlobalExceptionHandler.handlePayloadTooLarge(PayloadTooLargeException)` - Payload size error handler
30. `GlobalExceptionHandler.handleUnsupportedMediaType(HttpMediaTypeNotSupportedException)` - Content type validation
31. `GlobalExceptionHandler.handleValidation(MethodArgumentNotValidException,WebRequest)` - Input validation error handler
32. `ApiLoggingFilter.doFilter(...)` - API request logging
33. `ExceptionHandlingFilter.doFilter(...)` - Exception handling in filters
34. `RequestCorrelationFilter.doFilterInternal(...)` - Request correlation ID tracking
35. `TempReservationExpiryListener.onMessage(Message,byte[])` - Redis message listener
36. `Location.onUpdate()` - Lifecycle callback (protected)
37. `AvailabilityService.getBulkAvailability(List<String>,Integer)` - Bulk availability calculation
38. `Base64EncodingService.encode(String)` - Utility encoding
39. `InventoryService.ship(String)` - Shipment processing (alternative signature)
40. `KafkaProducerService.sendReservationSoftHoldReleasedEvent` - Reservation soft-hold release events

### Unused Classes (30 identified)
All response wrapper classes (e.g., `ResponseEntity<...>`), exception classes, and DTOs with no callers. These are framework-generated return types that appear as "unused" but are actually standard Spring MVC patterns.

### Interfaces With No Implementors (6 identified)
```
- InventoryStateRepository
- LocationRepository
- MovementLedgerRepository
- ReservationRepository
- ReservedAggRepository
- SafetyStockPolicyRepository
```
**Note**: These Spring Data JPA repository interfaces are auto-generated proxies at runtime and are not "dead code" — they are invoked via Spring's dependency injection.

---

## Modernization Phases [Prescriptive]

### Phase 1: Stabilize & Document (Foundation)
**Goal**: Baseline current behavior, establish migration safety net

**Key Changes:**
- Document all 40 uncalled methods with business impact assessment
- Add integration tests for high-coupling methods:
  - `LocationService.updateLocation` (coupling 23)
  - `ReservationService.confirmReservation` (coupling 20)
  - `KafkaProducerService` (messaging hub)
- Establish code quality gates for coupling scores (&lt;15 threshold)
- Document Kafka topic schemas and event contracts

**Key Risks:**
- Incomplete understanding of business logic in high-coupling methods
- Redis TTL mechanism for temp reservations may have hidden dependencies
- No observable migration rollback mechanism

**Success Criteria:**
- 100% method coverage on top 10 hotspots
- Event flow architecture documented
- Migration rollback playbook validated

---

### Phase 2: Extract Location Management (Lowest Hanging Fruit)
**Goal**: Isolate location CRUD from core inventory logic

**Key Changes:**
- Extract `LocationService` and `LocationController` to dedicated service
- Create `location-service` microservice with:
  - Own database schema (location CRUD operations)
  - Expose REST API for location management
  - Publish `LOCATION_UPDATE` event on location changes
- Update `LocationEntity` to emit events via `LocationUpdatedEvent`

**Key Risks:**
- Location updates cascade to inventory state calculations
- `InventoryState` has composite keys dependent on `Location`
- Breaking existing tests that mock `LocationRepository`

**Success Criteria:**
- Location service operates independently
- Event-driven decoupling verified via Kafka
- No regression in location-based inventory lookups

---

### Phase 3: Decouple Reservation Lifecycle (Medium Complexity)
**Goal**: Isolate reservation management from inventory state

**Key Changes:**
- Split `ReservationService` into:
  - `ReservationService` (lifecycle management: CREATE, CONFIRM, CANCEL, RELEASE)
  - `ReservationQueryService` (read-only reservation status queries)
- Extract `ReservedAgg` to separate aggregate for ATP calculations
- Create `reservation-service` microservice:
  - Own reservation entity storage
  - Expose reservation CRUD APIs
  - Publish `RESERVATION_CONFIRMED`, `RESERVATION_CANCELLED` events
- Implement eventual consistency for reservation → inventory state sync

**Key Risks:**
- Temp reservations use Redis TTL — need migration strategy for TTL-based expiry
- `ReservedAgg` affects ATP calculations across multiple services
- Transaction boundary complexity: reservation creation + inventory reservation

**Success Criteria:**
- Reservation service operates independently
- Redis TTL mechanism migrated to service-side or database-backed TTL
- All reservation operations verified via integration tests
- ATP calculations remain accurate with async updates

---

### Phase 4: Extract Safety Stock Management (Configuration Engine)
**Goal**: Isolate safety stock policy engine from core operations

**Key Changes:**
- Extract `SafetyStockPolicy` as configurable business rule engine
- Create `safety-stock-service` microservice:
  - Policy CRUD operations (CREATE, UPDATE, QUERY)
  - Policy evaluation service (minQty, ruleType, effective dates)
  - Publish `SAFETY_STOCK_POLICY_UPDATED` events
- Move policy validation logic from `InventoryService` to policy evaluation service
- Decouple policy checks from movement recording

**Key Risks:**
- Safety stock policies affect ATP calculations
- Policy effective dates add temporal complexity
- `SafetyStockService` currently emits Kafka events — need re-routing

**Success Criteria:**
- Policy service operates independently
- Policy changes trigger async updates to affected services
- All policy validation logic unit-tested
- No policy validation regressions in production movements

---

### Phase 5: Centralize Event Production (Cross-Cutting Concern)
**Goal**: Extract KafkaProducerService to dedicated messaging infrastructure

**Key Changes:**
- Extract `KafkaProducerService` to `event-publishing-service` or leverage platform event bus
- Standardize event schemas across all services:
  - `MovementRecordedEvent`
  - `ReservationSoftHeldEvent`
  - `ReservationConfirmedEvent`
  - `ReservationCancelledEvent`
  - `SafetyStockPolicyUpdatedEvent`
- Implement consumer re-registration for services consuming events
- Add Dead Letter Queue (DLQ) for failed event processing

**Key Risks:**
- All services depend on KafkaProducerService — widespread coupling
- Event ordering guarantees may be affected by split infrastructure
- Consumer re-registration timing critical for migration

**Success Criteria:**
- All event producers standardized
- Kafka topics and schemas documented in schema registry
- DLQ handling implemented and tested
- Consumer re-registration validated

---

### Phase 6: Cleanup & Decommission
**Goal**: Remove dead code, consolidate legacy patterns

**Key Changes:**
- Remove or document unused methods (40 identified)
- Consolidate duplicate encoding/encoding utilities
- Review and remove redundant exception handlers
- Standardize DTO/Response patterns across endpoints
- Remove `Base64EncodingService` if unused by external consumers

**Key Risks:**
- May remove methods with hidden production dependencies
- Exception handler reorganization may miss edge cases
- DTO consolidation may break external consumers

**Success Criteria:**
- Codebase reduced by 15-20% through cleanup
- All public APIs documented
- Exception handling standardized with minimal try-catch blocks
- Performance improvement verified (reduced class load time)

---

## Migration Anti-Patterns [Inferred]

### Pattern 1: Tight Coupling Through Shared Dependencies
**Symptoms**: `LocationService`, `ReservationService`, and `SafetyStockService` all depend on `InventoryStateRepository`, `MovementLedgerRepository`, and `KafkaProducerService`.

**Migration Impact**: Cannot extract services in isolation — requires coordinated migration of all dependent services.

**Mitigation**: Execute migrations in reverse dependency order:
1. Extract `KafkaProducerService` first (event infrastructure)
2. Then extract services with lower coupling (Location)
3. Finally extract high-coupling services (Reservation, SafetyStock)

---

### Pattern 2: Temporal Reservation TTL via Redis
**Symptoms**: `TempReservationExpiryListener` listens to Redis for TTL-based reservation expiry.

**Migration Impact**: Redis-based TTL mechanism requires migration to service-side or database-backed TTL in new architecture. May require dual-write during migration.

**Mitigation**: Implement dual-write during phase 3 migration:
- Keep Redis TTL for existing reservations
- Start database-backed TTL for new reservations
- Migrate remaining Redis TTL to database over time

---

### Pattern 3: Business Logic in Controllers
**Symptoms**: Controller methods invoke services directly without clear separation, e.g., `InventoryController` manages multiple operations (movement, reservation, transfer).

**Migration Impact**: Makes unit testing difficult and obscures service boundaries.

**Mitigation**: During Phase 2-5, refactor controllers to:
- Delegate all business logic to services
- Use DTOs for request/response binding
- Apply validation via Jakarta `@Validated` and DTO annotations

---

### Pattern 4: Event Publishing From Multiple Sources
**Symptoms**: `KafkaProducerService` is called from `MovementService`, `ReservationService`, and `SafetyStockService`.

**Migration Impact**: Central point of failure — all services depend on single messaging infrastructure.

**Mitigation**: During Phase 6:
- Extract `KafkaProducerService` to dedicated messaging service
- Implement producer re-registry mechanism
- Add DLQ and retry logic for failed events
