<?php

use App\Http\Controllers\OrderController;

Route::get('/orders/{orderId}', [OrderController::class, 'show']);

Route::get('/orders/{orderId}/export', [OrderController::class, 'export']);

Route::middleware('auth')->group(function () {
    Route::post('/orders', 'OrderController@store');
});
