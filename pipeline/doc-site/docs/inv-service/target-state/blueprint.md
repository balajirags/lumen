# Target State Blueprint — Microservices Architecture

## Target Service Map [Prescriptive]

| Service Name | Responsibility | Data Owned | Events Published | Events Consumed |
|------|----|----|------|----|---|
| **location-service** | Manage warehouse locations, address management, location metadata | `Location` entity, `LocationType` enum | `LOCATION_UPDATED`, `LOCATION_CREATED`, `LOCATION_DELETED` | `INVENTORY_STATE_CREATED` (for auto-location validation) |
| **inventory-core-service** | Core inventory state management, ATP calculations, stock levels | `InventoryState`, `InventoryStateId` | `INVENTORY_STATE_UPDATED`, `INVENTORY_STATE_CREATED`, `ATP_CALCULATED` | `LOCATION_UPDATED`, `RESERVATION_SOFT_HELD`, `RESERVATION_CONFIRMED`, `RESERVATION_CANCELLED`, `RESERVATION_RELEASED`, `MOVEMENT_RECORDED` |
| **movement-service** | Record stock movements, inbound/outbound transfers, movement history | `MovementLedger`, `MovementType` enum | `MOVEMENT_RECORDED`, `INBOUND_ADDED`, `OUTBOUND_REMOVED` | `INVENTORY_STATE_UPDATED` (for state synchronization) |
| **reservation-service** | Create, confirm, cancel, release reservations; soft-hold management | `Reservation`, `ReservationStatus` enum, `ReservedAgg` | `RESERVATION_SOFT_HELD`, `RESERVATION_CONFIRMED`, `RESERVATION_CANCELLED`, `RESERVATION_RELEASED` | `LOCATION_UPDATED`, `INVENTORY_STATE_UPDATED` (for availability checks) |
| **safety-stock-service** | Safety stock policy engine, minQty rules, effective date management | `SafetyStockPolicy`, `SafetyStockPolicyId`, `RuleType` enum | `SAFETY_STOCK_POLICY_UPDATED`, `SAFETY_STOCK_CHECK_COMPLETED` | `INVENTORY_STATE_UPDATED`, `LOCATION_UPDATED` |
| **availability-service** | ATP availability calculations, bulk availability queries | Aggregate: `InventoryState`, `ReservedAgg`, `SafetyStockPolicy` data | `AVAILABILITY_QUERY_COMPLETED` | `INVENTORY_STATE_UPDATED`, `RESERVATION_...`, `SAFETY_STOCK_POLICY_UPDATED` |
| **event-publishing-service** | Centralized Kafka message producers, schema validation, DLQ handling | Event schemas, topic configurations | N/A (produces events via other services) | N/A |
| **audit-service** | Reconciliation snapshots, audit trails, compliance reporting | `ReconciliationSnapshot` data (aggregated from all sources) | `AUDIT_SNAPSHOT_COMPLETED` | `INVENTORY_STATE_UPDATED`, `MOVEMENT_RECORDED`, `SAFETY_STOCK_POLICY_UPDATED` |

**Note**: All services use `PostgreSQL` for persistence with `Spring Data JPA` repositories. Messaging uses `Apache Kafka` for async event propagation.

---

## Migration Principles [Prescriptive]

### Database-Per-Service Pattern
- Each service owns its database schema and tables
- **location-service**: `locations` table, `location_types` reference
- **inventory-core-service**: `inventory_states` table, `inventory_states_id` composite keys
- **movement-service**: `movement_ledgers` table, `movement_types` reference
- **reservation-service**: `reservations` table, `reservation_statuses` enum, `reserved_agg` table
- **safety-stock-service**: `safety_stock_policies` table, `safety_stock_policy_ids` composite, `rule_types` reference
- **Shared reference tables**: `location_types`, `reservation_statuses` may be shared via read-only replicas or canonical source of truth

### API-First Design
- All services expose REST APIs with OpenAPI/Swagger documentation
- Request/Response DTOs follow consistent naming conventions
- Error responses use unified `ErrorResponse` DTO structure
- Pagination for list endpoints with `Page<T>` return types

### Eventual Consistency
- Reservation → inventory state updates are eventually consistent
- Safety stock policy changes trigger async recomputation of ATP
- Movements update inventory state asynchronously to avoid transactional locks
- TTL-based reservation expiration handled via service-side polling or database triggers

### API Composition & Aggregation
- **availability-service** aggregates data from:
  - `inventory-core-service` (current stock levels)
  - `reservation-service` (reserved quantities)
  - `safety-stock-service` (policy thresholds)
- **audit-service** aggregates reconciliation snapshots on-demand from all inventory-related services
- Cross-service calls implemented with `Resilience4j` circuit breakers and `Spring Retry` retry logic

### Strangler Fig Pattern
- Gradually migrate endpoints from monolith to microservices
- Phase 1: Route new inventory operations to `location-service`
- Phase 2: Extract reservation CRUD operations to `reservation-service`
- Phase 3: Migrate safety stock management to `safety-stock-service`
- Phase 4: Extract movement recording to `movement-service`
- Phase 5: Decommission monolithic endpoints as strangler completes

### Eventual Consistency Backpressure
- Implement `Circuit Breaker` pattern for dependency failures
- Use `Spring Retry` with exponential backoff for transient failures
- All services implement health checks (`/health`, `/ready`)
- Use `Kafka` DLQ for failed event processing with alerting

### API Versioning
- Use URL path versioning (`/api/v1/`, `/api/v2/`)
- Deprecation strategy with 6-month sunset periods
- `@Deprecated` annotations on all legacy endpoints
- Contract-first OpenAPI specification for service contracts

### Observability Standards
- All services implement:
  - Structured JSON logging (`application.yml` config)
  - Distributed tracing via `OpenTelemetry` headers (`X-Request-ID`, `X-Correlation-ID`)
  - Prometheus metrics (`metrics.yml` endpoints)
  - Health endpoints (`/health`, `/ready`)
- Centralized log aggregation via `ELK` or `Loki` stack
- Metrics dashboards for business KPIs: reservation lifecycle, ATP calculations, safety stock rule violations

---

## Open Questions [Unknown]

### Redis TTL Migration Strategy
**Question**: How should Redis-based TTL expiration mechanism for temp reservations be migrated to database-backed TTL or service-side polling?

**Required Information**:
- Current Redis TTL configuration (TTL minutes, expiry keys, listener patterns)
- Volume of temp reservations and frequency of expiry checks
- Business requirement for near-instant expiry vs. eventual consistency is acceptable

**Recommended Approach**: Dual-write TTL to both Redis and database during migration window, then migrate consumers to prefer database TTL.

---

### Event Ordering Guarantees
**Question**: Does the business require strict ordering guarantees for reservation movements?

**Required Information**:
- Business requirement for FIFO ordering of reservation confirmations
- Whether async ATP recomputation is acceptable or requires synchronous validation
- Whether movement recording must be synchronous with inventory state update

**Impact**: Affects choice between Kafka with strict ordering vs. eventual consistency for high-throughput paths.

---

### Safety Stock Policy Validation Timing
**Question**: Should safety stock policy validation occur synchronously (blocking movement recording) or asynchronously (non-blocking with validation failures)?

**Required Information**:
- Business requirement for hard vs. soft safety stock constraints
- Whether policy violations require manual approval or auto-rejection
- Latency requirements for movement recording (must be &lt;100ms vs. &lt;3 seconds)

**Impact**: Affects whether safety stock service operates synchronously or asynchronously in the movement flow.

---

### Audit Snapshot Aggregation Strategy
**Question**: Should audit reconciliation be computed on-demand or via batch processing?

**Required Information**:
- Frequency of reconciliation snapshots (real-time vs. daily batch)
- Performance requirements for reconciliation queries
- Whether snapshots need to be cached for performance

**Impact**: Affects whether separate `audit-service` is needed or if existing services can compute snapshots on-demand.

---

### Schema Registry Governance
**Question**: Who owns and governs event schema evolution (Avro/Protobuf schemas)?

**Required Information**:
- Team structure (single platform team vs. distributed teams)
- Schema registry tool choice (Confluent Schema Registry vs. Apicurio vs. custom)
- Policy for backward/forward compatibility

**Impact**: Affects event publishing architecture and migration tooling.

---

### Cross-Service Transaction Boundaries
**Question**: For reservation → inventory state sync, what is the acceptable consistency model?

**Required Information**:
- Whether reservation confirmation must be atomic with inventory reservation
- Whether partial failures (reservation created, inventory reservation fails) are acceptable
- Business impact of overselling due to async sync delays

**Impact**: Determines whether to use SAGA pattern with compensating transactions or accept eventual consistency with manual reconciliation.

---

## Implementation Notes

### Service Communication Patterns
- **Synchronous API calls**: Use REST with `RestTemplate` or `WebClient` for:
  - Location lookups during reservation creation
  - Safety stock policy validation during movement recording
  - Bulk availability queries for ATP calculations
- **Async event-driven**: Kafka topics for:
  - Movement recording → inventory state sync
  - Reservation lifecycle → ATP updates
  - Policy updates → availability recalculations

### Technology Stack (Per Service)
- **Framework**: Spring Boot 3.x with Spring MVC REST
- **Persistence**: Spring Data JPA with PostgreSQL (schema per service)
- **Messaging**: Apache Kafka with Spring Kafka
- **Cache**: Redis for reservation TTL and API response caching
- **Observability**: OpenTelemetry + Prometheus + Grafana
- **Security**: Spring Security with JWT authentication/authorization

### Gaps in Graph Analysis
The following recommendations require additional business context or external documentation:
- Exact TTL timeout for temp reservations (currently `TEMP_RESERVATION_TTL_MINUTES` — need business value)
- Policy validation rules beyond minQty and ruleType enum values
- API versioning strategy for existing clients (how many clients depend on current endpoints)
- Compliance requirements for audit trails (retention periods, encryption standards)
- Disaster recovery requirements (RTO/RPO for each service)
