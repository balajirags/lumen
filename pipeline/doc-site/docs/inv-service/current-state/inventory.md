# Current State Inventory

## Technology Stack [Observed]

| Category | Technology |
|----------|------------|
| **Framework** | Spring Boot 3.x (via `@SpringBootApplication`) |
| **REST** | Spring MVC (`@RestController`, `@GetMapping`, `@PostMapping`, `@PutMapping`) |
| **Persistence** | Spring Data JPA (via `@Repository`, `JpaRepository`) |
| **Cache** | Redis (via `@Configuration` + `RedisConfig`) |
| **Messaging** | Apache Kafka (via `KafkaProducerService`) |
| **API Docs** | OpenAPI/Swagger (via `@Operation`, `@ApiResponse`, `@Tag`) |
| **Data Model** | Lombok (`@Data`, `@Builder`, `@AllArgsConstructor`, `@NoArgsConstructor`) |
| **Transaction Management** | Spring `@Transactional` |

---

## API Surface [Observed]

### PasswordController
| Path | Method | Description |
|------|--------|-------------|
| `/api/passwords/generate` | `GET` | Generate password for security operations |

### AvailabilityController
| Path | Method | Description |
|------|--------|-------------|
| `/api/availability/{sku}/locations/{locationId}` | `GET` | Get inventory availability for a specific SKU and location |
| `/api/availability/bulk` | `POST` | Get inventory availability for multiple SKUs in bulk |

### EncodeController
| Path | Method | Description |
|------|--------|-------------|
| `/api/encode/base64` | `POST` | Encode request data using base64 encoding |

### InventoryController (primary business endpoint)
| Path | Method | Description |
|------|--------|-------------|
| `/api/inventory/states/{sku}/{locationId}` | `GET` | Get current inventory state by SKU and location |
| `/api/inventory/reconciliation/{sku}/{locationId}` | `GET` | Get reconciliation snapshot for inventory audit |
| `/api/inventory/movements` | `GET` | List movement history with pagination and date filtering |
| `/api/inventory/skus/distinct` | `GET` | Get list of all distinct SKUs in inventory |
| `/api/inventory/reservations/{sku}` | `GET` | Get reservation status for a SKU |
| `/api/inventory/reservations` | `GET` | List all reservations with pagination |
| `/api/inventory/reservation/create` | `POST` | Create a new inventory reservation |
| `/api/inventory/reservation/cancel` | `POST` | Cancel an existing reservation |
| `/api/inventory/reservation/confirm` | `POST` | Confirm a temporary reservation permanently |
| `/api/inventory/reservation/release` | `POST` | Release a confirmed reservation |
| `/api/inventory/movement/record` | `POST` | Record an inventory movement (add/remove) |
| `/api/inventory/transfer` | `POST` | Transfer inventory between locations |
| `/api/inventory/ship` | `POST` | Process outbound shipment |

### LocationController
| Path | Method | Description |
|------|--------|-------------|
| `/api/locations` | `GET` | List all warehouse locations |
| `/api/locations` | `POST` | Create a new location |
| `/api/locations/{id}` | `PUT` | Update an existing location |

### SafetyStockController
| Path | Method | Description |
|------|--------|-------------|
| `/api/safety-stock/{sku}/{locationId}` | `GET` | Get safety stock policy for a SKU at a location |
| `/api/safety-stock/{sku}/{locationId}` | `PUT` | Update safety stock policy for a SKU |
| `/api/safety-stock/policy` | `POST` | Create a new safety stock policy |

---

## Module Structure [Observed]

| Package | Classes | Dtos | Interfaces | Purpose |
|---------|---------|------|------------|---------|
| `com.inventory.inventoryservice` | 1 | - | - | Main application entry point (`InventoryServiceApplication`) |
| `com.inventory.inventoryservice.api` | 1 | - | - | Password generation utility API |
| `com.inventory.inventoryservice.config` | 1 | - | - | Configuration (Redis, CORS) |
| `com.inventory.inventoryservice.controller` | 5 | - | - | REST API endpoints (6 controllers total including API) |
| `com.inventory.inventoryservice.dto` | 18 | 18 | - | Request/Response DTOs (11 request, 7 response types) |
| `com.inventory.inventoryservice.exception` | 2 | - | - | Exception handling (`GlobalExceptionHandler`, `PayloadTooLargeException`) |
| `com.inventory.inventoryservice.filter` | 3 | - | - | Request filters (logging, correlation, exception handling) |
| `com.inventory.inventoryservice.listener` | 1 | - | - | Event listener (temporary reservation expiry) |
| `com.inventory.inventoryservice.model` | 9 | - | - | Domain entities (6 JPA entities, 3 enums) |
| `com.inventory.inventoryservice.repository` | - | - | 6 | Data access interfaces (Spring Data JPA repositories) |
| `com.inventory.inventoryservice.service` | 8 | - | - | Business logic services (7 services + utility) |
| `com.inventory.inventoryservice.util` | 1 | - | - | Utility services (password generation) |

**Total:** 12 packages, 42 classes, 18 DTOs, 6 repository interfaces, 56 annotated types

---

## Domain Entities [Observed]

### InventoryState
- **Identifier:** `id` (Long)
- **sku** (String) - Stock keeping unit identifier
- **locationId** (Long) - Associated warehouse location
- **onHand** (Integer) - Current available quantity
- **quarantine** (Integer) - Quarantined quantity
- **damaged** (Integer) - Damaged quantity
- **inbound** (Integer) - Quantity in transit
- **updatedAt** (OffsetDateTime) - Last update timestamp

### MovementLedger
- **Identifier:** `id` (Long)
- **sku** (String) - Item SKU
- **locationId** (Long) - Location of movement
- **type** (MovementType enum) - Movement type (ADD, REMOVE, TRANSFER, etc.)
- **qty** (Integer) - Quantity moved
- **referenceId** (String) - Reference document ID
- **source** (String) - Transaction source system/user
- **createdAt** (OffsetDateTime) - Movement timestamp

### Reservation
- **Identifier:** `id` (UUID)
- **orderId** (String) - Order reference
- **sku** (String) - Reserved SKU
- **locationId** (Long) - Location of reserved inventory
- **qty** (Integer) - Reserved quantity
- **status** (ReservationStatus enum) - PENDING, CONFIRMED, RELEASED, CANCELLED, EXPIRED
- **createdAt** (OffsetDateTime) - Creation timestamp

### SafetyStockPolicy
- **Identifier:** Composite key (`sku` + `locationId`)
- **sku** (String) - Item SKU
- **locationId** (Long) - Location identifier
- **minQty** (Integer) - Minimum stock level
- **ruleType** (String) - Rule classification
- **effectiveFrom** (OffsetDateTime) - Policy start date
- **effectiveTo** (OffsetDateTime) - Policy expiry date

### Location
- **Identifier:** `id` (Long)
- **name** (String) - Location name
- **locationType** (LocationType enum) - Type classification
- **active** (boolean) - Activates/deactivates the location
- **addressLine1** (String) - Street address
- **addressLine2** (String) - Address line 2
- **city** (String) - City
- **stateProvince** (String) - State/province
- **postalCode** (String) - Postal/zip code
- **countryCode** (String) - ISO country code
- **latitude** (BigDecimal) - Geographic latitude
- **longitude** (BigDecimal) - Geographic longitude
- **createdAt** (Instant) - Creation timestamp
- **updatedAt** (Instant) - Last update timestamp

### Enums

**ReservationStatus**: (status states for reservations)
- PENDING, CONFIRMED, RELEASED, CANCELLED, EXPIRED

**MovementType**: (types of inventory movements)
- ADD, REMOVE, TRANSFER, RETURN, ADJUSTMENT, DAMAGED

**LocationType**: (category of physical locations)
- WAREHOUSE, DISTRIBUTION_CENTER, RETAIL_STORE, PICKUP_POINT, TRANSIT_HUB

---

## Technology Stack [Observed]
_(See technology table at top - documented only in this file per requirements)_

---

## Cross-Cutting Concerns [Observed]

### Filters (Spring MVC Filters)
| Filter | Purpose |
|--------|---------|
| `RequestCorrelationFilter` | Adds correlation IDs for request tracing across services |
| `ApiLoggingFilter` | Logs HTTP requests/responses for auditing |
| `ExceptionHandlingFilter` | Catches and standardizes error responses |

### Exception Handler (`GlobalExceptionHandler`)
| Exception | Handler Method | Behavior |
|-----------|----------------|----------|
| `HttpMessageNotReadableException` | `handleMalformedJson` | Returns 400 for invalid JSON |
| `MethodArgumentNotValidException` | `handleValidation` | Returns 400 with validation errors |
| `HttpMediaTypeNotSupportedException` | `handleUnsupportedMediaType` | Returns 415 for wrong content type |
| `IllegalArgumentException` | `handleIllegalArgument` | Returns 400 for bad arguments |
| `PayloadTooLargeException` | `handlePayloadTooLarge` | Returns 413 for oversized payloads |
| `Exception` (general) | `handleGeneral` | Returns 500 for unhandled exceptions |

### Event Listeners
| Listener | Trigger | Purpose |
|----------|---------|---------|
| `TempReservationExpiryListener` | Redis key expiration | Auto-expires temporary reservations |

### Configuration
| Configuration | Component | Purpose |
|---------------|-----------|---------|
| `RedisConfig` | RedisConnectionFactory + MessageListenerAdapter | Redis cache + event subscriber setup |
| CORS Configurer | `InventoryServiceApplication.corsConfigurer()` | Cross-origin request configuration |

### Caching Strategy
- Redis-based caching for frequently accessed inventory state data
- Event-driven invalidation via Redis key expiration

---

## DTOs Overview

### Request DTOs
- `MovementRequest` - Record inventory movement
- `ShipmentRequest` - Process outbound shipment  
- `TransferRequest` - Transfer items between locations
- `ReservationCreateRequest` - Create new reservation
- `ReservationCancelRequest` - Cancel reservation
- `ReservationConfirmRequest` - Confirm reservation
- `ReservationReleaseRequest` - Release reservation
- `SafetyStockPolicyRequest` - Configure safety stock
- `BulkAvailabilityRequest` - Bulk availability query
- `EncodeRequest` - Base64 encoding request

### Response DTOs
- `AvailabilityResponse` - Current availability for SKU(s)
- `BulkAvailabilityResponse` - Bulk availability results
- `InventoryStateResponse` - Full inventory state
- `ReconciliationSnapshotResponse` - Reconciliation data
- `ReservationStatusResponse` - Reservation status
- `SafetyStockPolicyResponse` - Safety stock configuration
- `EncodeResponse` - Encoded data
- `ErrorResponse` - Standardized error format

---

## Annotations Summary

| Annotation | Count | Primary Use |
|------------|-------|-------------|
| `@Data` (Lombok) | 27 | Auto-generate getters/setters/equals |
| `@PostMapping` | 11 | REST endpoint mapping |
| `@GetMapping` | 10 | REST endpoint mapping |
| `@Service` | 8 | Business layer component |
| `@Transactional` | 7 | Database transaction boundaries |
| `@Repository` | 5 | Data access layer |
| `@Entity` | 6 | JPA entity mapping |
| `@Operation` | 13 | OpenAPI documentation |
| `@Configuration` | 1 | Spring configuration class |
| `@RestControllerAdvice` | 1 | Centralized exception handling |

---

## Known Dependencies

### No Circular Dependencies [Observed]
- **Package-level**: No circular dependencies detected
- **Class-level**: No mutual call cycles detected

### External Systems
- **Apache Kafka**: Producer service publishes events for inventory changes
- **Redis**: Cache and event subscription for reservation expiry
- **PostgreSQL/Relational DB**: Primary data store (via Spring Data JPA)
