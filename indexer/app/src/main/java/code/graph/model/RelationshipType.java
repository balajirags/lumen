package code.graph.model;

public enum RelationshipType {
    // Common structural
    CONTAINS,           // Package -> Class, Class -> Method/Field, Module -> Function
    EXTENDS,            // Class -> Class
    IMPLEMENTS,         // Class -> Interface
    CALLS,              // Method -> Method, Function -> Function
    RETURNS,            // Method -> Type
    HAS_PARAMETER,      // Method -> Parameter
    OF_TYPE,            // Field/Parameter -> Type
    HAS_ANNOTATION,     // Any -> Annotation (Java), Any -> Decorator (Python)
    OVERRIDES,          // Method -> Method
    THROWS,             // Method -> Exception type
    // CPG-specific
    SOURCE_FILE,        // Type -> File it is defined in
    AST_CHILD,          // Parent -> Child in syntax tree
    CFG_NEXT,           // Statement -> Next statement (control flow)
    DATA_FLOW,          // Variable definition -> use
    // JavaScript/React
    IMPORTS,            // Module -> Module (ES imports)
    EXPORTS,            // Module -> exported symbol
    RENDERS,            // Component -> Component (JSX usage)
    USES_HOOK,          // Component -> Hook
    PROP_DEPENDENCY,    // Component -> prop type/value
    // Python
    DECORATES,          // Decorator -> Function/Class
    YIELDS,             // Generator -> yielded type
    // Kotlin
    EXTENSION_OF,       // Extension function -> receiver type
    DELEGATES_TO,       // Property delegation (by keyword)
    SEALED_SUBTYPE,     // Sealed class/interface -> permitted subtype
    COMPANION_OF,       // Companion object -> containing class
    SUSPENDS            // Suspend function coroutine call
}
