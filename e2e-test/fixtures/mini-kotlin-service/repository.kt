package app

class OrderRepository {
    fun findOrder(id: String): Order {
        return Order(id, "pending")
    }

    suspend fun findOrderAsync(id: String): Order {
        return findOrder(id)
    }
}
