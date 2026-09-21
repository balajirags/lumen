"""Graph database backends: KuzuBackend, LadybugBackend, and Neo4jBackend."""

import os

# kuzu's and ladybug's native (pybind11) extensions cannot coexist in one
# process AT ALL, permanently, once either has been imported -- verified
# empirically: `import kuzu` after `import ladybug` raises
# "generic_type: type Database is already registered", and the reverse order
# fails too (a failed kuzu.Database() open attempt against a LadybugDB file
# leaves ladybug unable to load its own C API afterward). A single `lumen
# run`/`lumen mcp` process only ever reads ONE kuzu_path for its whole
# lifetime today, so this never triggers in practice -- this guard exists so
# a future mixed-format scenario (e.g. an MCP server serving multiple repos)
# fails with a clear message instead of a cryptic pybind11 ImportError.
_loaded_native_backend: str | None = None


def _require_native_backend(kind: str) -> None:
    global _loaded_native_backend
    if _loaded_native_backend is not None and _loaded_native_backend != kind:
        raise RuntimeError(
            f"Cannot open a {kind} database: this process already loaded {_loaded_native_backend}'s "
            f"native extension for a different database. kuzu and ladybug cannot coexist in one "
            f"process -- serve/process each format in a separate process."
        )
    _loaded_native_backend = kind


# ── Graph Backends ──────────────────────────────────────────────────────────


class KuzuBackend:
    def __init__(self, db_path: str, read_only: bool = True):
        _require_native_backend("kuzu")
        import kuzu

        self.db_path = os.path.abspath(db_path)
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(f"KuzuDB database not found at {self.db_path}")
        self.db = kuzu.Database(self.db_path, read_only=read_only)
        self.conn = kuzu.Connection(self.db)

    def execute(self, cypher: str) -> list[dict]:
        result = self.conn.execute(cypher)
        columns = result.get_column_names()
        rows = []
        while result.has_next():
            values = result.get_next()
            rows.append(dict(zip(columns, values)))
        return rows

    def close(self):
        pass


class LadybugBackend:
    """Reads a graph written by the experimental treesitter-indexer prototype
    (treesitter-indexer/graph_store.py), which targets LadybugDB rather than
    the archived kuzu package. LadybugDB preserves Kuzu's embedded API and
    Cypher dialect (verified: label(n), DISTINCT/ORDER BY aliasing, etc. all
    behave identically), so ReverseEngineerToolkit's Cypher works unchanged
    against this backend -- but the two engines' on-disk file formats are
    NOT interchangeable (verified empirically: kuzu.Database() cannot open a
    LadybugDB-written file and vice versa), so the correct backend must be
    selected per-database -- see create_graph_backend().
    """

    def __init__(self, db_path: str, read_only: bool = True):
        _require_native_backend("ladybug")
        import ladybug as lb

        self.db_path = os.path.abspath(db_path)
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(f"LadybugDB database not found at {self.db_path}")
        self.db = lb.Database(self.db_path, read_only=read_only)
        self.conn = lb.Connection(self.db)

    def execute(self, cypher: str) -> list[dict]:
        result = self.conn.execute(cypher)
        columns = result.get_column_names()
        rows = []
        while result.has_next():
            values = result.get_next()
            rows.append(dict(zip(columns, values)))
        return rows

    def close(self):
        pass


_MAGIC_KUZU = b"KUZU"
_MAGIC_LADYBUG = b"LBUG"


def sniff_backend_kind(db_path: str) -> str:
    """Determines a database's on-disk format from its file header, WITHOUT
    importing either client library. This matters because kuzu's and
    ladybug's native extensions cannot safely coexist in one process --
    verified empirically: even a *failed* kuzu.Database() open attempt
    (e.g. against a LadybugDB file) leaves the process unable to load
    ladybug's C API afterward, and importing both directly raises
    "generic_type: type Database is already registered". A magic-byte peek
    avoids ever touching either library during detection.
    """
    path = db_path
    if os.path.isdir(path):
        # Older Kuzu versions store the DB as a directory rather than a
        # single file; peek at the first regular file found inside it.
        for name in sorted(os.listdir(path)):
            candidate = os.path.join(path, name)
            if os.path.isfile(candidate):
                path = candidate
                break
    try:
        with open(path, "rb") as fh:
            header = fh.read(4)
    except OSError:
        return "kuzu"  # fall through to KuzuBackend's own not-found error
    return "ladybug" if header == _MAGIC_LADYBUG else "kuzu"


def create_graph_backend(db_path: str, backend: str | None = None, read_only: bool = True):
    """Strategy-pattern factory: returns the backend matching db_path's actual
    on-disk format. Pass `backend` ("kuzu" | "ladybug") explicitly to skip
    the file-header sniff when the format is already known; otherwise it is
    auto-detected via sniff_backend_kind (never by probing with the client
    libraries themselves -- see that function's docstring for why).
    """
    kind = backend or sniff_backend_kind(db_path)
    if kind == "ladybug":
        return LadybugBackend(db_path, read_only=read_only)
    return KuzuBackend(db_path, read_only=read_only)


class Neo4jBackend:
    def __init__(self, uri: str, username: str, password: str, database: str = "neo4j"):
        from neo4j import GraphDatabase

        self.driver = GraphDatabase.driver(uri, auth=(username, password))
        self.database = database

    def execute(self, cypher: str) -> list[dict]:
        with self.driver.session(database=self.database) as session:
            result = session.run(cypher)
            return [dict(record) for record in result]

    def close(self):
        self.driver.close()
