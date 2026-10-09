<?php

namespace App\Repositories;

use App\Models\Order;

class OrderRepository
{
    public static function find(int $id): Order
    {
        return new Order($id);
    }

    public static function findWithBilling(int $id): Order
    {
        $order = self::find($id);
        // Interpolated (not concatenated) deliberately -- tree-sitter-php
        // parses a double-quoted interpolated string as a single
        // `encapsed_string` node whose first `string_content` child is the
        // literal prefix before the interpolation, which is exactly what
        // _string_literal_text() reads (same reason Python's fixture uses
        // an f-string, not `"..." + id`, for its own billing-URL case --
        // confirmed against this project's own tested behavior: the
        // detected URL is the literal prefix only, "https://billing.
        // internal/orders/", without the trailing $id -- a `.`-concatenated
        // string would instead parse as a `binary_expression`, which
        // _string_literal_text() does not recognize at all, so nothing
        // would be detected).
        $billing = file_get_contents("https://billing.internal/orders/$id");
        return $order;
    }
}
