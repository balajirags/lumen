import { OrderRepository, findOrderRecord } from './repository';
import { fetchBillingInfo } from './billing';

// A module-level instance, not `this` -- repo.findOrder(...) below is the
// documented 0.50 scope-boundary case (property-mediated, non-`this`
// receiver), same posture as PHP's mini-php-service fixture's analogous
// constructor-injected repository property.
const repo = new OrderRepository();

export function showOrder(req, res) {
  const order = repo.findOrder(req.params.id);
  res.send(order.id);
}

export const exportOrder = (req, res) => {
  const order = findOrderRecord(req.params.id);
  fetchBillingInfo(req.params.id);
  res.send(order.id);
};
