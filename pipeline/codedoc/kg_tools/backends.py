"""Graph database backends: KuzuBackend and Neo4jBackend."""

import os


# ── Graph Backends ──────────────────────────────────────────────────────────


class KuzuBackend:
    def __init__(self, db_path: str, read_only: bool = True):
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
