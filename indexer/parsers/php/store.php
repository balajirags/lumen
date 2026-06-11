<?php
/**
 * KuzuDB store for the PHP parser.
 * Bridges PHP graph output to KuzuDB by delegating to kuzu_writer.py,
 * which reuses the Python parser's KuzuStore (same kuzu Python package).
 */

class PhpKuzuStore {

    private string $dbPath;
    private bool   $clear;
    private string $writerScript;

    public function __construct(string $dbPath, bool $clear = false) {
        $this->dbPath       = $dbPath;
        $this->clear        = $clear;
        $this->writerScript = __DIR__ . '/kuzu_writer.py';
    }

    public function save(array $graph): void {
        $pythonCmd = $this->findPython();
        if ($pythonCmd === null) {
            throw new RuntimeException(
                'Python 3 not found. Python is required for KuzuDB writes from the PHP parser.'
            );
        }
        if (!file_exists($this->writerScript)) {
            throw new RuntimeException(
                "KuzuDB writer not found at {$this->writerScript}"
            );
        }

        $tmpFile = tempnam(sys_get_temp_dir(), 'lumen_php_') . '.json';
        try {
            file_put_contents($tmpFile, json_encode($graph, JSON_UNESCAPED_SLASHES));

            $cmd = array_values(array_filter([
                $pythonCmd,
                $this->writerScript,
                '--db-path', $this->dbPath,
                '--json-file', $tmpFile,
                $this->clear ? '--clear' : null,
            ]));

            $proc = proc_open($cmd, [
                0 => ['pipe', 'r'],
                1 => ['pipe', 'w'],
                2 => STDERR,        // pass writer stderr directly through
            ], $pipes);

            if (!is_resource($proc)) {
                throw new RuntimeException('Failed to launch kuzu_writer.py');
            }

            fclose($pipes[0]);
            $stdout = stream_get_contents($pipes[1]);
            fclose($pipes[1]);
            $exitCode = proc_close($proc);

            if ($stdout !== '') {
                fwrite(STDERR, $stdout);
            }
            if ($exitCode !== 0) {
                throw new RuntimeException(
                    "KuzuDB write failed (kuzu_writer.py exited with code $exitCode)"
                );
            }
        } finally {
            if (file_exists($tmpFile)) {
                @unlink($tmpFile);
            }
        }
    }

    private function findPython(): ?string {
        $candidates = ['python3', 'python',
                       '/opt/homebrew/bin/python3', '/usr/local/bin/python3',
                       '/usr/bin/python3'];
        foreach ($candidates as $cmd) {
            $output = []; $code = 0;
            @exec(escapeshellcmd($cmd) . ' --version 2>&1', $output, $code);
            if ($code === 0 && str_contains(implode('', $output), 'Python 3')) {
                return $cmd;
            }
        }
        return null;
    }
}


function create_store(string $backend, string $dbPath, bool $clear = false): PhpKuzuStore {
    if ($backend !== 'kuzu') {
        throw new InvalidArgumentException("Unsupported backend: $backend");
    }
    return new PhpKuzuStore($dbPath, $clear);
}
