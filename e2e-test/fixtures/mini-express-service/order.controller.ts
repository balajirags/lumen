// NestJS decorator-based routing is always written in TypeScript in real
// code (decorators parse with a fundamentally different AST shape in
// plain JS -- nested inside method_definition -- vs. TS -- a positional
// sibling in class_body -- verified empirically; only the TS shape is
// supported, matching real-world NestJS usage).
export class OrderController {
  @Get('/orders')
  list() {
    return [];
  }
}
