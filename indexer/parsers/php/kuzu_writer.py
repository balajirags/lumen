#!/usr/bin/env python3
"""
Standalone KuzuDB writer for the PHP parser.
Called by store.php to write a PHP graph JSON file to KuzuDB.
Reuses the KuzuStore class from the Python parser.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'python'))
from store import KuzuStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db-path', required=True)
    parser.add_argument('--json-file', required=True)
    parser.add_argument('--clear', action='store_true')
    args = parser.parse_args()

    with open(args.json_file) as f:
        graph_json = json.load(f)

    store = KuzuStore(args.db_path)
    if args.clear:
        store.clear()
    else:
        store.init_schema()
    store.save(graph_json)


if __name__ == '__main__':
    main()
