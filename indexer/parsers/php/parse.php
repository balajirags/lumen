#!/usr/bin/env php
<?php
/**
 * PHP source code parser for lumen code graph.
 * Uses nikic/php-parser for AST analysis.
 *
 * Usage: php parse.php <directory> [options]
 *   --backend kuzu|neo4j|json   Graph backend (default: json)
 *   --db-path <path>            KuzuDB database path
 *   --clear                     Clear existing graph before writing
 *   --repo-name <name>          Repository name override
 */

ini_set('memory_limit', '1G');

require_once __DIR__ . '/vendor/autoload.php';

use PhpParser\Node;
use PhpParser\Node\Stmt;
use PhpParser\Node\Expr;
use PhpParser\NodeTraverser;
use PhpParser\NodeVisitor\NameResolver;
use PhpParser\NodeVisitorAbstract;
use PhpParser\ParserFactory;
use PhpParser\Error;

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

function parseArgs(array $argv): array {
    $args = ['directory' => null, 'backend' => 'kuzu', 'db_path' => null,
             'clear' => false, 'repo_name' => null];
    $i = 1;
    while ($i < count($argv)) {
        $a = $argv[$i];
        if ($a === '--backend')      { $args['backend']   = $argv[++$i] ?? 'kuzu'; }
        elseif ($a === '--db-path')  { $args['db_path']   = $argv[++$i] ?? null; }
        elseif ($a === '--clear')    { $args['clear']     = true; }
        elseif ($a === '--repo-name'){ $args['repo_name'] = $argv[++$i] ?? null; }
        elseif (!str_starts_with($a, '--')) { $args['directory'] = $a; }
        $i++;
    }
    return $args;
}

// ---------------------------------------------------------------------------
// Graph builder
// ---------------------------------------------------------------------------

class CodeGraphBuilder {
    public array $nodes = [];
    public array $relationships = [];
    public array $internalClasses = [];   // fqn -> nodeId
    public array $internalFunctions = []; // fqn -> nodeId
    public array $classParents = [];      // childFqn -> parentFqn (EXTENDS)

    public function addNode(string $id, string $type, string $name,
                            string $qualifiedName, array $props = []): void {
        if (!isset($this->nodes[$id])) {
            $this->nodes[$id] = [
                'id'            => $id,
                'type'          => $type,
                'name'          => $name,
                'qualifiedName' => $qualifiedName,
                // Use stdClass for empty props so json_encode emits {} not []
                'properties'    => empty($props) ? new \stdClass() : $props,
            ];
        }
    }

    public function addRelationship(string $sourceId, string $targetId,
                                    string $type, array $props = []): void {
        $this->relationships[] = [
            'sourceId'   => $sourceId,
            'targetId'   => $targetId,
            'type'       => $type,
            // Use stdClass for empty props so json_encode emits {} not []
            'properties' => empty($props) ? new \stdClass() : $props,
        ];
    }

    public function hasNode(string $id): bool {
        return isset($this->nodes[$id]);
    }

    public function toJson(): array {
        return [
            'nodes'         => array_values($this->nodes),
            'relationships' => $this->relationships,
        ];
    }
}

// ---------------------------------------------------------------------------
// normKind / kind helpers
// ---------------------------------------------------------------------------

function normKind(string $type): string {
    return match($type) {
        'MODULE'                         => 'CodeUnit',
        'CLASS', 'INTERFACE'             => 'TypeLike',
        'FUNCTION', 'METHOD',
        'CONSTRUCTOR', 'ASYNC_FUNCTION'  => 'Callable',
        'FIELD'                          => 'DataMember',
        'FILE'                           => 'SourceFile',
        'DECORATOR'                      => 'AnnotationLike',
        default                          => 'CodeElement',
    };
}

function kindLabel(string $type): string {
    return match($type) {
        'MODULE'      => 'Module',
        'CLASS'       => 'Class',
        'INTERFACE'   => 'Interface',
        'FUNCTION'    => 'Function',
        'METHOD'      => 'Method',
        'CONSTRUCTOR' => 'Constructor',
        'FIELD'       => 'Field',
        'FILE'        => 'File',
        'DECORATOR'   => 'Decorator',
        default       => $type,
    };
}

function baseProps(string $type, string $path, ?int $line = null,
                   array $extra = []): array {
    $p = [
        'language' => 'php',
        'kind'     => kindLabel($type),
        'normKind' => normKind($type),
        'external' => false,
        'path'     => $path,
    ];
    if ($line !== null) $p['lineNumber'] = $line;
    return array_merge($p, $extra);
}

// ---------------------------------------------------------------------------
// Ignored path check
// ---------------------------------------------------------------------------

const ALWAYS_IGNORED = ['vendor', 'node_modules', '.git', 'storage', '.idea',
                        '__pycache__', 'bootstrap'.DIRECTORY_SEPARATOR.'cache'];

function isIgnored(string $relPath): bool {
    $parts = explode(DIRECTORY_SEPARATOR, $relPath);
    foreach (ALWAYS_IGNORED as $dir) {
        if (in_array($dir, $parts, true)) return true;
    }
    // Ignore test directories at root
    if (isset($parts[0]) && in_array($parts[0], ['tests', 'test', 'spec'], true)) return true;
    return false;
}

// ---------------------------------------------------------------------------
// File-level visitor
// ---------------------------------------------------------------------------

class PhpFileVisitor extends NodeVisitorAbstract {
    private CodeGraphBuilder $graph;
    private string $relPath;
    private string $rootDir;
    private string $fileId;

    // Scope tracking
    private ?string $currentNs      = null;  // current namespace FQCN
    private ?string $currentClass   = null;  // current class FQCN
    private ?string $currentMethod  = null;  // current method nodeId

    // Deferred call edges (collected and resolved after all files parsed)
    public array $pendingCalls = [];  // [sourceId, callTarget, line, confidence]

    // Annotations queued for controller methods from route files
    public array $routeAnnotations = []; // methodNodeId -> [{annName, path}]

    public function __construct(CodeGraphBuilder $graph, string $relPath, string $rootDir) {
        $this->graph   = $graph;
        $this->relPath = $relPath;
        $this->rootDir = $rootDir;
        $this->fileId  = 'file:' . str_replace('\\', '/', $relPath);
    }

    // -- Namespace -----------------------------------------------------------

    public function enterNode(Node $node) {
        if ($node instanceof Stmt\Namespace_) {
            $this->currentNs = $node->name ? $node->name->toString() : null;
            $moduleId = 'module:' . ($this->currentNs ?? basename($this->relPath, '.php'));
            if (!$this->graph->hasNode($moduleId)) {
                $this->graph->addNode(
                    $moduleId, 'MODULE',
                    $this->currentNs ?? basename($this->relPath, '.php'),
                    $this->currentNs ?? basename($this->relPath, '.php'),
                    baseProps('MODULE', $this->relPath, $node->getStartLine())
                );
            }
            // MODULE → FILE
            $this->graph->addRelationship($moduleId, $this->fileId, 'SOURCE_FILE');
        }

        // -- Class / Interface ------------------------------------------------
        if ($node instanceof Stmt\Class_ || $node instanceof Stmt\Interface_ ||
            $node instanceof Stmt\Trait_) {

            if ($node->name === null) return; // anonymous class — skip
            $fqn  = $this->qualify($node->name->name);
            $type = ($node instanceof Stmt\Interface_) ? 'INTERFACE' : 'CLASS';

            $extra = [];
            if ($node instanceof Stmt\Trait_) $extra['phpKind'] = 'trait';
            if ($node instanceof Stmt\Class_) {
                if ($node->isAbstract()) $extra['isAbstract'] = true;
                if ($node->isFinal())    $extra['isFinal']    = true;
            }

            $nodeId = strtolower($type) . ':' . $fqn;
            $this->graph->addNode($nodeId, $type, $node->name->name, $fqn,
                baseProps($type, $this->relPath, $node->getStartLine(), $extra));

            // Register in graph's class index
            $this->graph->internalClasses[$fqn] = $nodeId;

            // MODULE/FILE → CLASS
            $moduleId = $this->currentModuleId();
            $this->graph->addRelationship($moduleId, $nodeId, 'CONTAINS');
            $this->graph->addRelationship($nodeId, $this->fileId, 'SOURCE_FILE');

            // EXTENDS
            if ($node instanceof Stmt\Class_ && $node->extends !== null) {
                $parentFqn = $node->extends->toString();
                $this->graph->classParents[$fqn] = $parentFqn;
                $parentId = 'class:' . $parentFqn;
                $this->graph->addRelationship($nodeId, $parentId, 'EXTENDS');
            }

            // IMPLEMENTS / trait USE → IMPLEMENTS rel
            $implementsList = [];
            if ($node instanceof Stmt\Class_)     $implementsList = $node->implements;
            if ($node instanceof Stmt\Interface_) $implementsList = $node->extends;
            foreach ($implementsList as $iface) {
                $ifaceFqn = $iface->toString();
                $ifaceId  = 'interface:' . $ifaceFqn;
                $this->graph->addRelationship($nodeId, $ifaceId, 'IMPLEMENTS');
            }

            $this->currentClass = $fqn;
        }

        // -- Trait use --------------------------------------------------------
        if ($node instanceof Stmt\TraitUse && $this->currentClass !== null) {
            $classId = 'class:' . $this->currentClass;
            foreach ($node->traits as $traitName) {
                $traitFqn = $traitName->toString();
                $traitId  = 'class:' . $traitFqn; // traits emitted as CLASS
                $this->graph->addRelationship($classId, $traitId, 'IMPLEMENTS',
                    ['traitUse' => true]);
            }
        }

        // -- Method -----------------------------------------------------------
        if ($node instanceof Stmt\ClassMethod && $this->currentClass !== null) {
            $methodName = $node->name->name;
            $isConstructor = $methodName === '__construct';
            $type   = $isConstructor ? 'CONSTRUCTOR' : 'METHOD';
            $fqn    = $this->currentClass . '.' . $methodName;
            $nodeId = strtolower($type) . ':' . $fqn;
            $extra  = [
                'visibility'  => $this->visibility($node->flags),
                'isStatic'    => (bool)($node->flags & Stmt\Class_::MODIFIER_STATIC),
                'isAbstract'  => (bool)($node->flags & Stmt\Class_::MODIFIER_ABSTRACT),
            ];
            $this->graph->addNode($nodeId, $type, $methodName, $fqn,
                baseProps($type, $this->relPath, $node->getStartLine(), $extra));

            $classId = 'class:' . $this->currentClass;
            $this->graph->addRelationship($classId, $nodeId, 'CONTAINS');
            $this->currentMethod = $nodeId;
        }

        // -- Global function --------------------------------------------------
        if ($node instanceof Stmt\Function_) {
            $fqn    = $this->qualify($node->name->name);
            $nodeId = 'function:' . $fqn;
            $this->graph->addNode($nodeId, 'FUNCTION', $node->name->name, $fqn,
                baseProps('FUNCTION', $this->relPath, $node->getStartLine()));
            $this->graph->internalFunctions[$fqn] = $nodeId;
            $moduleId = $this->currentModuleId();
            $this->graph->addRelationship($moduleId, $nodeId, 'CONTAINS');
            $this->currentMethod = $nodeId;
        }

        // -- Property ---------------------------------------------------------
        if ($node instanceof Stmt\Property && $this->currentClass !== null) {
            $classId = 'class:' . $this->currentClass;
            foreach ($node->props as $prop) {
                $name   = $prop->name->name;
                $fqn    = $this->currentClass . '.' . $name;
                $nodeId = 'field:' . $fqn;
                $extra  = ['visibility' => $this->visibility($node->flags)];
                if ($node->type !== null) {
                    $extra['fieldType'] = $this->typeToString($node->type);
                }
                $this->graph->addNode($nodeId, 'FIELD', $name, $fqn,
                    baseProps('FIELD', $this->relPath, $prop->getStartLine(), $extra));
                $this->graph->addRelationship($classId, $nodeId, 'CONTAINS');
            }
        }

        // -- Class constant ---------------------------------------------------
        if ($node instanceof Stmt\ClassConst && $this->currentClass !== null) {
            $classId = 'class:' . $this->currentClass;
            foreach ($node->consts as $const) {
                $name   = $const->name->name;
                $fqn    = $this->currentClass . '.' . $name;
                $nodeId = 'field:' . $fqn;
                $this->graph->addNode($nodeId, 'FIELD', $name, $fqn,
                    baseProps('FIELD', $this->relPath, $const->getStartLine(),
                        ['phpKind' => 'constant', 'visibility' => $this->visibility($node->flags)]));
                $this->graph->addRelationship($classId, $nodeId, 'CONTAINS');
            }
        }

        // -- Calls (method/static/function) -----------------------------------
        $this->collectCall($node);
    }

    public function leaveNode(Node $node) {
        if ($node instanceof Stmt\Class_ || $node instanceof Stmt\Interface_ ||
            $node instanceof Stmt\Trait_) {
            $this->currentClass  = null;
            $this->currentMethod = null;
        }
        if ($node instanceof Stmt\ClassMethod || $node instanceof Stmt\Function_) {
            $this->currentMethod = null;
        }
        if ($node instanceof Stmt\Namespace_) {
            $this->currentNs = null;
        }
    }

    // -- Call collection -----------------------------------------------------

    private function collectCall(Node $node): void {
        if ($this->currentMethod === null) return;

        // $this->method() or $this->method() → same class
        if ($node instanceof Expr\MethodCall &&
            $node->var instanceof Expr\Variable &&
            $node->var->name === 'this' &&
            $node->name instanceof Node\Identifier &&
            $this->currentClass !== null) {
            $target = $this->currentClass . '.' . $node->name->name;
            $this->pendingCalls[] = [
                'source'     => $this->currentMethod,
                'target'     => 'method:' . $target,
                'line'       => $node->getStartLine(),
                'confidence' => 0.95,
                'reason'     => 'this-call',
            ];
        }

        // ClassName::staticMethod() — after NameResolver FQN is resolved
        if ($node instanceof Expr\StaticCall &&
            $node->class instanceof Node\Name &&
            $node->name instanceof Node\Identifier) {
            $className = $node->class->toString();
            if (!in_array($className, ['self', 'static', 'parent'], true)) {
                $target = $className . '.' . $node->name->name;
                $this->pendingCalls[] = [
                    'source'     => $this->currentMethod,
                    'target'     => 'method:' . $target,
                    'line'       => $node->getStartLine(),
                    'confidence' => 0.90,
                    'reason'     => 'static-call',
                ];
            } elseif (in_array($className, ['self', 'static'], true) &&
                      $this->currentClass !== null) {
                $target = $this->currentClass . '.' . $node->name->name;
                $this->pendingCalls[] = [
                    'source'     => $this->currentMethod,
                    'target'     => 'method:' . $target,
                    'line'       => $node->getStartLine(),
                    'confidence' => 0.95,
                    'reason'     => 'self-call',
                ];
            }
        }

        // free function call()
        if ($node instanceof Expr\FuncCall &&
            $node->name instanceof Node\Name) {
            $fqn = $node->name->toString();
            $this->pendingCalls[] = [
                'source'     => $this->currentMethod,
                'target'     => 'function:' . $fqn,
                'line'       => $node->getStartLine(),
                'confidence' => 0.90,
                'reason'     => 'func-call',
            ];
        }

        // new ClassName() — constructor call
        if ($node instanceof Expr\New_ &&
            $node->class instanceof Node\Name) {
            $fqn = $node->class->toString();
            $ctorId = 'constructor:' . $fqn . '.__construct';
            $this->pendingCalls[] = [
                'source'     => $this->currentMethod,
                'target'     => $ctorId,
                'line'       => $node->getStartLine(),
                'confidence' => 0.90,
                'reason'     => 'new-call',
            ];
        }
    }

    // -- Helpers -------------------------------------------------------------

    private function qualify(string $name): string {
        return $this->currentNs ? $this->currentNs . '\\' . $name : $name;
    }

    private function currentModuleId(): string {
        $ns = $this->currentNs ?? basename($this->relPath, '.php');
        return 'module:' . $ns;
    }

    private function visibility(int $flags): string {
        if ($flags & Stmt\Class_::MODIFIER_PRIVATE)   return 'private';
        if ($flags & Stmt\Class_::MODIFIER_PROTECTED)  return 'protected';
        return 'public';
    }

    private function typeToString(?Node $type): string {
        if ($type === null) return '';
        if ($type instanceof Node\Name)            return $type->toString();
        if ($type instanceof Node\Identifier)      return $type->toString();
        if ($type instanceof Node\NullableType)    return '?' . $this->typeToString($type->type);
        if ($type instanceof Node\UnionType)       return implode('|', array_map([$this, 'typeToString'], $type->types));
        if ($type instanceof Node\IntersectionType) return implode('&', array_map([$this, 'typeToString'], $type->types));
        return '';
    }
}

// ---------------------------------------------------------------------------
// Route file extractor (Laravel-style)
// ---------------------------------------------------------------------------

class RouteExtractor extends NodeVisitorAbstract {
    public array $routes = []; // [{httpMethod, path, controllerFqn, actionMethod}]

    // HTTP method name → annotation name (matches WorkflowBuilder's HTTP_ANNOTATIONS set)
    private const METHOD_MAP = [
        'get'    => 'GetMapping',
        'post'   => 'PostMapping',
        'put'    => 'PutMapping',
        'delete' => 'DeleteMapping',
        'patch'  => 'PatchMapping',
        'any'    => 'RequestMapping',
        'match'  => 'RequestMapping',
    ];

    public function enterNode(Node $node) {
        // Route::get('/path', [Controller::class, 'method'])
        // Route::post('/path', [Controller::class, 'method'])
        if (!($node instanceof Expr\StaticCall)) return;
        if (!($node->class instanceof Node\Name)) return;
        if ($node->class->toString() !== 'Illuminate\\Support\\Facades\\Route' &&
            $node->class->getLast() !== 'Route') return;
        if (!($node->name instanceof Node\Identifier)) return;

        $httpVerb = strtolower($node->name->name);
        if (!isset(self::METHOD_MAP[$httpVerb])) return;
        $annName = self::METHOD_MAP[$httpVerb];

        $args = $node->args;
        if (count($args) < 2) return;

        // First arg: route path (string)
        $pathArg = $args[0]->value ?? null;
        $path = $this->extractString($pathArg);
        if ($path === null) return;

        // Second arg: [Controller::class, 'method'] array or 'Controller@method' string
        $handlerArg = $args[1]->value ?? null;
        $resolved = $this->resolveHandler($handlerArg, $annName, $path);
        if ($resolved !== null) {
            $this->routes[] = $resolved;
        }
    }

    private function resolveHandler(?Node $handler, string $annName, string $path): ?array {
        if ($handler === null) return null;

        // [Controller::class, 'method']
        if ($handler instanceof Expr\Array_ && count($handler->items) === 2) {
            $classExpr  = $handler->items[0]->value ?? null;
            $methodExpr = $handler->items[1]->value ?? null;
            $methodName = $this->extractString($methodExpr);

            if ($classExpr instanceof Expr\ClassConstFetch &&
                $classExpr->name instanceof Node\Identifier &&
                $classExpr->name->name === 'class' &&
                $classExpr->class instanceof Node\Name &&
                $methodName !== null) {
                return [
                    'annName'         => $annName,
                    'path'            => $path,
                    'controllerFqn'   => $classExpr->class->toString(),
                    'actionMethod'    => $methodName,
                ];
            }
        }

        // 'Controller@method' string
        if ($handler instanceof Node\Scalar\String_) {
            $parts = explode('@', $handler->value, 2);
            if (count($parts) === 2) {
                return [
                    'annName'       => $annName,
                    'path'          => $path,
                    'controllerFqn' => $parts[0],
                    'actionMethod'  => $parts[1],
                ];
            }
        }

        return null;
    }

    private function extractString(?Node $node): ?string {
        if ($node instanceof Node\Scalar\String_) return $node->value;
        return null;
    }
}

// ---------------------------------------------------------------------------
// Raw-PHP entry point detector ($_GET / $_POST / $_REQUEST access)
// ---------------------------------------------------------------------------

class HttpSuperglobalDetector extends NodeVisitorAbstract {
    public array $entryMethods = []; // methodNodeId or functionNodeId

    private ?string $currentCallable = null;

    private const SUPERGLOBALS = ['_GET', '_POST', '_REQUEST', '_SERVER'];

    public function enterNode(Node $node) {
        if ($node instanceof Stmt\ClassMethod || $node instanceof Stmt\Function_) {
            $this->currentCallable = null; // set from context after visitor run
        }
        if ($node instanceof Expr\ArrayDimFetch &&
            $node->var instanceof Expr\Variable &&
            in_array($node->var->name, self::SUPERGLOBALS, true) &&
            $this->currentCallable !== null) {
            if (!in_array($this->currentCallable, $this->entryMethods, true)) {
                $this->entryMethods[] = $this->currentCallable;
            }
        }
    }

    public function setCurrentCallable(?string $id): void {
        $this->currentCallable = $id;
    }
}

// ---------------------------------------------------------------------------
// Main parser
// ---------------------------------------------------------------------------

function collectPhpFiles(string $rootDir): array {
    $files = [];
    $iter  = new RecursiveIteratorIterator(
        new RecursiveDirectoryIterator($rootDir, FilesystemIterator::SKIP_DOTS),
        RecursiveIteratorIterator::SELF_FIRST
    );
    foreach ($iter as $fileInfo) {
        if (!$fileInfo->isFile()) continue;
        if ($fileInfo->getExtension() !== 'php') continue;
        $relPath = ltrim(substr($fileInfo->getPathname(), strlen($rootDir)), DIRECTORY_SEPARATOR);
        if (isIgnored($relPath)) continue;
        $files[] = $relPath;
    }
    sort($files);
    return $files;
}

function emitFileNode(CodeGraphBuilder $graph, string $relPath): string {
    $fileId = 'file:' . str_replace('\\', '/', $relPath);
    $graph->addNode($fileId, 'FILE', basename($relPath), $relPath,
        baseProps('FILE', $relPath));
    return $fileId;
}

function parseDirectory(string $rootDir): CodeGraphBuilder {
    $parser  = (new ParserFactory())->create(ParserFactory::PREFER_PHP7);
    $graph   = new CodeGraphBuilder();
    $allFiles = collectPhpFiles($rootDir);

    fwrite(STDERR, sprintf("Parsing PHP files in: %s (%d files)\n", $rootDir, count($allFiles)));

    $visitors      = [];
    $routeFiles    = [];  // relPath for route-file post-processing
    $superglobalHits = []; // methodNodeId with superglobal access

    // ---- Pass 1: parse all files -------------------------------------------
    foreach ($allFiles as $relPath) {
        $absPath = $rootDir . DIRECTORY_SEPARATOR . $relPath;
        $code    = @file_get_contents($absPath);
        if ($code === false) continue;

        try {
            $stmts = $parser->parse($code);
            if ($stmts === null) continue;
        } catch (Error $e) {
            fwrite(STDERR, "  Warning: parse error in $relPath: " . $e->getMessage() . "\n");
            continue;
        }

        $fileId  = emitFileNode($graph, $relPath);
        $traverser = new NodeTraverser();
        $traverser->addVisitor(new NameResolver(null, ['preserveOriginalNames' => false]));
        $visitor = new PhpFileVisitor($graph, $relPath, $rootDir);
        $traverser->addVisitor($visitor);
        $traverser->traverse($stmts);

        $visitors[$relPath] = ['visitor' => $visitor, 'stmts' => $stmts];

        // Mark route files
        $normRel = str_replace('\\', '/', $relPath);
        if (str_starts_with($normRel, 'routes/') ||
            str_starts_with($normRel, 'route/')) {
            $routeFiles[] = $relPath;
        }
    }

    // ---- Pass 2: resolve CALLS edges ---------------------------------------
    foreach ($visitors as $data) {
        /** @var PhpFileVisitor $visitor */
        $visitor = $data['visitor'];
        foreach ($visitor->pendingCalls as $call) {
            $targetId = $call['target'];
            // Only emit if target is an internal node
            if (!$graph->hasNode($targetId)) continue;
            $graph->addRelationship(
                $call['source'], $targetId, 'CALLS',
                ['lineNumber' => $call['line'],
                 'confidence' => $call['confidence'],
                 'reason'     => $call['reason']]
            );
        }
    }

    // ---- Pass 3: route annotation injection (Laravel) ----------------------
    foreach ($routeFiles as $relPath) {
        $data    = $visitors[$relPath] ?? null;
        if ($data === null) continue;
        $stmts   = $data['stmts'];
        $traverser = new NodeTraverser();
        $traverser->addVisitor(new NameResolver(null, ['preserveOriginalNames' => false]));
        $routeExtractor = new RouteExtractor();
        $traverser->addVisitor($routeExtractor);
        $traverser->traverse($stmts);

        foreach ($routeExtractor->routes as $route) {
            $controllerFqn = $route['controllerFqn'];
            $actionMethod  = $route['actionMethod'];
            $methodNodeId  = 'method:' . $controllerFqn . '.' . $actionMethod;

            // Emit ANNOTATION_TYPE node (canonical per annotation name).
            // ANNOTATION_TYPE is required by the HAS_ANNOTATION schema (Method → AnnotationType).
            $annId = 'annotation:' . $route['annName'];
            if (!$graph->hasNode($annId)) {
                $graph->addNode($annId, 'ANNOTATION_TYPE', $route['annName'],
                    $route['annName'],
                    ['language' => 'php', 'kind' => 'AnnotationType',
                     'normKind' => 'AnnotationLike', 'external' => false, 'path' => '']);
            }

            // Emit HAS_ANNOTATION edge even if target method not yet in graph
            // (cross-package controllers discovered after route file parsing)
            if (!$graph->hasNode($methodNodeId)) {
                // Create a placeholder method node so the workflow builder can find it
                $classId = 'class:' . $controllerFqn;
                $graph->addNode($methodNodeId, 'METHOD', $actionMethod,
                    $controllerFqn . '.' . $actionMethod,
                    baseProps('METHOD', '', null,
                        ['external' => false, 'language' => 'php',
                         'inferred' => true]));
                if ($graph->hasNode($classId)) {
                    $graph->addRelationship($classId, $methodNodeId, 'CONTAINS');
                }
            }

            $graph->addRelationship($methodNodeId, $annId, 'HAS_ANNOTATION',
                ['value' => $route['path']]);

            fwrite(STDERR, sprintf("  Route: %s %s → %s::%s\n",
                $route['annName'], $route['path'], $controllerFqn, $actionMethod));
        }
    }

    fwrite(STDERR, sprintf("  Nodes: %d, Relationships: %d\n",
        count($graph->nodes), count($graph->relationships)));

    return $graph;
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

$args = parseArgs($argv);

if ($args['directory'] === null) {
    fwrite(STDERR, "Usage: php parse.php <directory> [--backend json] [--repo-name name]\n");
    exit(1);
}

$rootDir = realpath($args['directory']);
if ($rootDir === false || !is_dir($rootDir)) {
    fwrite(STDERR, "Error: directory not found: {$args['directory']}\n");
    exit(1);
}

$graph = parseDirectory($rootDir);

if ($args['backend'] === 'json') {
    echo json_encode($graph->toJson(), JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES);
    echo "\n";
} elseif ($args['backend'] === 'kuzu') {
    if (empty($args['db_path'])) {
        fwrite(STDERR, "Error: --db-path is required when using --backend kuzu\n");
        exit(1);
    }
    // Match the Python/JS convention: --db-path is the parent directory;
    // the actual DB lives at <db-path>/<repo-name>-db
    $repoName = $args['repo_name'] ?? basename($rootDir);
    $dbPath   = rtrim($args['db_path'], DIRECTORY_SEPARATOR) . DIRECTORY_SEPARATOR . $repoName . '-db';

    require_once __DIR__ . '/store.php';
    $store = create_store('kuzu', $dbPath, $args['clear']);
    $store->save($graph->toJson());
} else {
    fwrite(STDERR, "Error: unsupported backend '{$args['backend']}'. Supported: kuzu, json\n");
    exit(1);
}
