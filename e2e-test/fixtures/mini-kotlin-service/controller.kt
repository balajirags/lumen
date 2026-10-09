package app

@RestController
class OrderController(private val repository: OrderRepository) {
    @GetMapping("/orders/{id}")
    suspend fun show(id: String): Order {
        return repository.findOrderAsync(id)
    }

    @GetMapping("/orders")
    fun list(): String {
        return "ok"
    }
}
