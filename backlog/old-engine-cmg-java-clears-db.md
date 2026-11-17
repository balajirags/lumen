# Old engine: cmg-java may wipe other languages' output in polyglot repos

Carried over from `backlog/treesitter-indexer-unified-switch.md` ("Related old-engine finding").
Unconfirmed by a test. `cmg-java`'s `--incremental` defaults to false, which clears the whole DB
(`KuzuGraphStore.clear()`), and `pipeline/codedoc/stages/indexer.py`'s legacy loop never passes
it. The loop order is by file count descending, so a polyglot repo where a non-JVM language has
more files runs that language first and `cmg-java` second, which could erase what the first
binary wrote. `e2e-test/test-lumen.sh` only has single-language fixtures, so nothing exercises it.

To do: reproduce with a polyglot fixture (e.g. `mini-fullstack-service` with the legacy engine),
and if confirmed pass `--incremental` for every language after the first or order JVM first.
