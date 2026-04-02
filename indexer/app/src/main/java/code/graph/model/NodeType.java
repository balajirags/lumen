package code.graph.model;

public enum NodeType {
    // Common
    PACKAGE,
    CLASS,
    INTERFACE,
    ENUM,
    RECORD,
    ANNOTATION_TYPE,
    METHOD,
    CONSTRUCTOR,
    FIELD,
    PARAMETER,
    // CPG-specific
    FILE,
    STATEMENT,
    // JavaScript/TypeScript
    MODULE,           // ES module
    FUNCTION,         // Standalone function
    ARROW_FUNCTION,   // Arrow function expression
    COMPONENT,        // React component (function or class)
    HOOK,             // React hook (useXxx)
    JSX_ELEMENT,      // JSX element usage
    // Python
    DECORATOR,        // Python decorator
    GENERATOR,        // Generator function
    ASYNC_FUNCTION,   // Async function
    COMPREHENSION,    // List/dict/set comprehension
    // Kotlin
    DATA_CLASS,       // Kotlin data class
    SEALED_CLASS,     // Kotlin sealed class
    SEALED_INTERFACE, // Kotlin sealed interface
    OBJECT_DECL,      // Kotlin object declaration (singleton)
    COMPANION_OBJECT, // Kotlin companion object
    EXTENSION_FUNCTION, // Kotlin extension function
    SUSPEND_FUNCTION, // Kotlin suspend function (coroutine)
    PROPERTY,         // Kotlin property with accessors
    LAMBDA,           // Lambda expression
    INIT_BLOCK,       // Init block in class
    TYPE_ALIAS        // Kotlin typealias
}
