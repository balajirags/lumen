<?php

namespace App\Services;

function dispatchToChannel($channel)
{
    // $channel's type is never declared or locally constructed -- this call
    // can only resolve via the 0.50 global-name fallback tier.
    $channel->notifyOrderReady();
}
