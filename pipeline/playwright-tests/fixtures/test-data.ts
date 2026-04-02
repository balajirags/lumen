// Shared request payloads for inventory movement E2E tests
// Derived from section 3b: Test Data Setup

// Base payload (section 3b)
export const BASE_PAYLOAD = {
  sku: 'SKU-001',
  locationId: 1,
  qty: 50,
  type: 'RECEIPT',
  referenceId: 'PO-2024-001',
  source: 'WMS',
};

// Movement type constants (derived from section 3b)
export const MOVEMENT_TYPES = {
  RECEIPT: 'RECEIPT',
  SHIPMENT: 'SHIPMENT',
  DAMAGE: 'DAMAGE',
  ADJUSTMENT: 'ADJUSTMENT',
} as const;

// Pre-seeded inventory states for happy paths (section 3c)
export const REQUIRED_INVENTORY_STATES = [
  { sku: 'SKU-001', locationId: 1, onHand: 100 },
  { sku: 'SKU-002', locationId: 2, onHand: 200 },
  { sku: 'SKU-ADJ', locationId: 3, onHand: 100 },
];

// Required location (section 3b)
export const REQUIRED_LOCATIONS = [
  { id: 1, name: 'Warehouse-1' },
];

// Mock boundary hints (section 3f)
export const MOCK_BOUNDED_DEPENDENCIES = {
  InventoryStateRepository: {
    findBySkuAndLocationId: {
      happy: 'return Optional.of(existingState)',
      error: 'return Optional.empty() to test auto-create',
    },
  },
  MovementLedgerRepository: {
    save: {
      happy: 'return saved entity with generated id and createdAt',
      error: 'throw DataIntegrityViolationException',
    },
  },
  KafkaProducerService: {
    sendMovementRecordedEvent: {
      happy: 'no-op / void',
      error: 'throw KafkaException',
    },
  },
};

export default BASE_PAYLOAD;
