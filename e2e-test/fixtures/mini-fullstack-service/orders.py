from fastapi import APIRouter

from channels import EmailChannel

router = APIRouter()


class Order:
    def __init__(self, order_id: int):
        self.order_id = order_id


@router.get("/orders/{order_id}")
def get_order(order_id: int) -> Order:
    """FastAPI-style route: the verb comes straight from the decorator's own
    name ("router.get" tokenizes to "get") -- zero extra mechanism needed,
    unlike Flask's methods=[...] kwarg. Also exercises the new typed
    Parameter/HAS_PARAMETER/RETURNS extraction (order_id: int, -> Order)."""
    notify_all(EmailChannel())
    return Order(order_id)


def notify_all(channel):
    """`channel`'s type is never declared or locally constructed -- this
    call can only resolve via the 0.50 global-name fallback tier."""
    channel.notify_user()
