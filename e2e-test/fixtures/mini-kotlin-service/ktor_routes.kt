package app

fun exportOrder(id: String): String {
    return fetchFromRepository(id)
}

fun fetchFromRepository(id: String): String {
    return "record:" + id
}

fun registerRoutes() {
    routing {
        get("/orders/{id}/export") {
            exportOrder("1")
        }
    }
}
