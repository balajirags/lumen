export function fetchBillingInfo(id) {
  // Interpolated (template literal) deliberately kept OUT of scope --
  // this engine's string-literal reader only recognizes the `string` node
  // type (single/double-quoted), not `template_string`, so a template
  // literal here would NOT be detected as an HTTP dependency. Using a
  // plain concatenation-free literal instead, so the detection actually
  // fires (same "known, accepted divergence" posture documented in
  // js/extractor.py).
  return fetch('https://billing.internal/orders');
}
