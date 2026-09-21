# Fixture repos for `test-lumen.sh`

Tiny, checked-in synthetic repos used by `../test-lumen.sh` as the default fast-path
target for every discovered `lumen` pipeline command. Kept intentionally small so indexing
and LLM calls stay fast and cheap — these are for catching pipeline/CLI/Makefile
regressions, not for exercising indexer/parser correctness at scale.

- `mini-flask-app/` — a 3-file Python Flask app (route handlers, an auth decorator, a
  repository class with an external HTTP call) with enough structure (entry points,
  decorators, external dependency, an unauthenticated route, a class with no incoming
  references) to give every pipeline's roles something real to find.

- `mini-spring-app/` — a 6-file synthetic Spring Boot app (`@RestController` with
  `@GetMapping`/`@PostMapping` entry points, a `@Service`, a `@Repository` BFS terminal, an
  interface + `@Component` implementation with `@Override`, and an `@Entity` with typed
  fields) used by `treesitter-indexer/scripts/compare_graphs.py` to validate the tree-sitter
  Java indexer prototype against the existing `cmg-java` indexer's output. Not currently
  wired into `test-lumen.sh`'s `FIXTURES` array, since the prototype is not part of any
  real pipeline yet.

Add more fixtures here (e.g. a small JS/TS repo) if a future pipeline needs
different structural signal than this one provides — `test-lumen.sh` runs every discovered
pipeline against every directory in its `FIXTURES` array.
