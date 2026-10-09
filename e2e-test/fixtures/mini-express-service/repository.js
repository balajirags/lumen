export class OrderRepository {
  findOrder(id) {
    return new Order(id);
  }
}

export class Order {
  constructor(id) {
    this.id = id;
  }
}

// A plain exported free function, alongside the class above -- used by
// exportOrder (see handlers.js) to demonstrate a REAL (>=0.9) cross-file
// workflow trace. OrderRepository.findOrder's realistic property-mediated
// access path (via handlers.js's module-level `repo` instance, not `this`)
// stays at the documented 0.50 scope-boundary tier -- this free function
// gives the fixture a second, genuinely resolvable path to the same
// Repository-named terminal.
export function findOrderRecord(id) {
  return new Order(id);
}
