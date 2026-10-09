<?php

namespace App\Services;

class NotificationChannel
{
    public function notifyOrderReady()
    {
        // The only notifyOrderReady() in this fixture -- called via a
        // duck-typed, untyped parameter in NotificationDispatcher, so it can
        // only resolve via the 0.50 global-name-fallback CALLS tier.
    }
}
