<?php

namespace App\Http\Controllers;

use App\Repositories\OrderRepository;

class OrderController
{
    public function __construct(private OrderRepository $repository)
    {
    }

    public function show(int $orderId): string
    {
        $order = $this->repository->find($orderId);
        return "order-" . $order->id;
    }

    public function store(): string
    {
        return "created";
    }

    public function export(int $orderId): string
    {
        // Direct static call (the Eloquent-style finder pattern, as common
        // in real PHP codebases as constructor-injected repositories) --
        // unlike show()'s property-mediated $this->repository->find() call
        // above, this resolves at a real tier (import-resolved, 0.90), so
        // it's the chain that actually demonstrates a full workflow trace
        // reaching a Repository-namespaced terminal.
        $order = OrderRepository::findWithBilling($orderId);
        return "exported-" . $order->id;
    }
}
