package app

class BillingClient {
    fun fetchBillingInfo(id: String): String {
        return fetchUrl("https://billing.internal/orders")
    }
}

fun fetchUrl(url: String): String {
    return url
}
