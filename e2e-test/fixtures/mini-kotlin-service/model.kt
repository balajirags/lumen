package app

data class Order(val id: String, val status: String)

sealed class PaymentResult

class PaymentSuccess : PaymentResult()

data class PaymentFailure(val reason: String) : PaymentResult()
