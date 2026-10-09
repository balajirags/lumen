<?php

namespace App\Models;

class Order
{
    public int $id;
    public string $status = "pending";

    public function __construct(int $id)
    {
        $this->id = $id;
    }
}
