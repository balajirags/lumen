---
title: "E2E Scenarios — Location Management"
type: "e2e-test-scenario"
flow: "location-management"
entry_point: "com.inventory.inventoryservice.controller.LocationController"
evidence: "Observed"
timestamp: "2025-01-24T00:00:00Z"
---

# Flow: Location Management

## 3a. Flow Summary [Observed]

Locations represent physical warehouses, stores, or distribution centres. The `LocationController` manages CRUD operations on these entities.

### Create Location
| Attribute | Value |
|---|---|
| **Entry Point** | `POST /locations` (inferred) |
| **Controller Method** | `LocationController.createLocation(Location)` — line 17 |
| **Returns** | `Location` (direct, no `ResponseEntity` wrapper observed) |
| **Service** | `LocationService.createLocation(Location)` — line 21 |

### Update Location
| Attribute | Value |
|---|---|
| **Entry Point** | `PUT /locations/{id}` (inferred) |
| **Controller Method** | `LocationController.updateLocation(Long id, Location body)` — line 27 |
| **Returns** | `Location` |
| **Service** | `LocationService.updateLocation(Long, Location)` — line 31 |

### List All Locations
| Attribute | Value |
|---|---|
| **Entry Point** | `GET /locations` (inferred) |
| **Controller Method** | `LocationController.getAllLocations()` — line 22 |
| **Returns** | `List<Location>` |
| **Service** | `LocationService.getAllLocations()` — line 26 |

### Call Chains (Observed)
```
createLocation(Location body)
  └─ LocationService.createLocation(Location)
       ├─ Location.getName() access (line 22) [validation or logging]
       └─ LocationRepository.save(location)    (inferred from RETURN at line 23)

getAllLocations()
  └─ LocationService.getAllLocations()
       └─ LocationRepository.findAll()          (inferred from line 27-28)

updateLocation(id, body)
  └─ LocationService.updateLocation(Long id, Location body)
       ├─ LocationRepository.findById(id)       (lines 32-33, inferred)
       ├─ Fields updated from body:
       │    name, locationType, isActive, addressLine1, addressLine2,
       │    city, stateProvince, postalCode, countryCode,
       │    latitude, longitude                 (lines 36-46)
       └─ LocationRepository.save(existing)     (inferred, line 48)
```

---

## 3b. Test Data Setup

### Create — Input Body — `Location` Entity
```json
{
  "name": "Warehouse North",
  "locationType": "WAREHOUSE",
  "isActive": true,
  "addressLine1": "123 Industrial Blvd",
  "addressLine2": "Unit 5",
  "city": "Chicago",
  "stateProvince": "IL",
  "postalCode": "60601",
  "countryCode": "US",
  "latitude": 41.8781,
  "longitude": -87.6298
}
```

### Update — Path + Body
```
PUT /locations/1
```
Body (same structure, partial update applied by service — new values overwrite existing)

### Location Fields [Observed]
| Field | Type | Notes |
|---|---|---|
| `id` | `Long` | Auto-generated PK |
| `name` | `String` | Required (accessed at line 22/36) |
| `locationType` | `LocationType` (enum) | Warehouse / Store / etc |
| `isActive` | `boolean` | Active flag |
| `addressLine1` | `String` | |
| `addressLine2` | `String` | Optional |
| `city` | `String` | |
| `stateProvince` | `String` | |
| `postalCode` | `String` | |
| `countryCode` | `String` | 2-letter ISO code (assumed) |
| `latitude` | `BigDecimal` | Geographic coordinate |
| `longitude` | `BigDecimal` | Geographic coordinate |
| `createdAt` | `Instant` | Auto-set on create |
| `updatedAt` | `Instant` | Auto-set on update |

---

## 3c. Happy Path Scenarios

### HP-1 (Create): Create a New Active Warehouse Location
**Given**: No location named "Warehouse North" exists

**When**: `POST /locations` with valid full payload

**Then**:
- HTTP 200/201 (inferred — returns `Location` directly)
- Response body: `Location` with generated `id`, `createdAt` populated
- `Location` persisted in DB
- No Kafka event (no messaging observed for locations)

### HP-2 (Create): Create with Minimal Required Fields
**Given**: Only `name`, `locationType`, `isActive` provided

**When**: `POST /locations` with minimal payload

**Then**:
- HTTP 200/201 OK
- `Location` created with null address fields (if allowed)

### HP-3 (Update): Update Location Name and Active Status
**Given**: `Location(id=1)` exists with `name="Old Warehouse"`, `isActive=true`

**When**: `PUT /locations/1` with `name="New Warehouse"`, `isActive=false`

**Then**:
- HTTP 200 OK
- Response body: updated `Location` entity
- `name` = "New Warehouse", `isActive` = false
- `updatedAt` refreshed [Hypothesized: auto-managed by `@PreUpdate` or set manually]

### HP-4 (Update): Update Geographic Coordinates
**Given**: Location exists

**When**: `PUT /locations/1` with new `latitude` and `longitude` values

**Then**:
- HTTP 200 OK
- Coordinates updated (lines 45-46 observed)

### HP-5 (List): Get All Locations
**Given**: 5 active and 2 inactive locations in DB

**When**: `GET /locations`

**Then**:
- HTTP 200 OK
- Response: list of 7 `Location` objects (all locations, no filter observed)

### HP-6 (List): Empty List When No Locations Exist
**Given**: Empty `location` table

**When**: `GET /locations`

**Then**:
- HTTP 200 OK
- Response: `[]` (empty array)

---

## 3d. Error Path Scenarios

### EP-1 (Update): Location Not Found
**Given**: `LocationRepository.findById(9999)` returns empty

**When**: `PUT /locations/9999`

**Then**:
- [Hypothesized]: `IllegalArgumentException` or `EntityNotFoundException` → HTTP 400 or 404
- Body: `ErrorResponse { message: "Location not found for id 9999" }`

### EP-2 (Create): Missing Required `name` Field
**Given**: Payload without `name` (null or blank)

**When**: `POST /locations`

**Then**:
- [Hypothesized]: `@NotBlank` or `@NotNull` validation → HTTP 400 via `handleValidation`
- Or service accesses `getName()` and gets null → `NullPointerException` → HTTP 500

### EP-3 (Create): Invalid `locationType` Enum Value
**Given**: `"locationType": "INVALID"`

**When**: `POST /locations`

**Then**:
- HTTP 400 — `handleMalformedJson` (`HttpMessageNotReadableException` during enum deserialization)

### EP-4 (Create): Duplicate Location Name (if uniqueness enforced)
**Given**: Location with `name="Warehouse North"` already exists

**When**: `POST /locations` with same name

**Then**:
- [Hypothesized]: If unique constraint on `name`: `DataIntegrityViolationException` → HTTP 500 via `handleGeneral`
- If no unique constraint: second location created with duplicate name (permitted)

### EP-5 (Update): Invalid `id` Type in Path
**Given**: `PUT /locations/notANumber`

**When**: Request sent

**Then**:
- HTTP 400 — Spring binding error for `Long` path variable

### EP-6 (Any): Malformed JSON Body
**Given**: Invalid JSON in request body

**When**: `POST /locations` or `PUT /locations/1`

**Then**:
- HTTP 400 — `handleMalformedJson`

---

## 3e. Edge Cases

### EC-1: Deactivate a Location with Active Reservations
**Given**:
- Location `id=1` is referenced by active `Reservation` or `InventoryState` records
- `PUT /locations/1` with `isActive=false`

**When**: Update request

**Then**:
- [Hypothesized]: Location deactivated without cascade (no FK cascade observed)
- Existing reservations/inventory unaffected — data remains valid
- Business question: should deactivation be blocked if active inventory exists? Flag for review.

### EC-2: Update Location's `countryCode` to Empty String
**Given**: Existing valid `countryCode="US"`
**When**: `PUT /locations/1` with `countryCode=""`
**Then**: [Hypothesized]: Empty string saved (if no `@NotBlank` on update path)

### EC-3: Extremely Long `name` (DB column overflow)
**Given**: `name` with 10,000 characters
**When**: `POST /locations`
**Then**: [Hypothesized]: DB column length constraint violation → HTTP 500 via `handleGeneral`

### EC-4: Latitude/Longitude Out of Range
**Given**: `latitude=999.99`, `longitude=-999.99`
**When**: `POST /locations`
**Then**: [Hypothesized]: Accepted (no range validation observed for `BigDecimal` fields)
- Assert: `@DecimalMin`/`@DecimalMax` validation should exist for geographic validity

### EC-5: Get Locations Filtered by Active Status
**Given**: Mix of active/inactive locations
**When**: `GET /locations` — no filter parameter available (no filter observed)
**Then**: All locations returned regardless of `isActive` status
- Potential enhancement: add `?active=true` filter

---

## 3f. Mock Boundaries

### Create
| Dependency | Method | Happy Path Stub | Error Path Stub |
|---|---|---|---|
| `LocationRepository` | `save(Location)` | Return entity with generated `id` and `createdAt` | Throw `DataIntegrityViolationException` |

### Update
| Dependency | Method | Happy Path Stub | Error Path Stub |
|---|---|---|---|
| `LocationRepository` | `findById(Long)` | Return `Optional.of(existingLocation)` | Return `Optional.empty()` |
| `LocationRepository` | `save(Location)` | Return updated entity | Throw `DataIntegrityViolationException` |

### List
| Dependency | Method | Happy Path Stub | Error Path Stub |
|---|---|---|---|
| `LocationRepository` | `findAll()` | Return list of 5 `Location` entities | Return empty list |
