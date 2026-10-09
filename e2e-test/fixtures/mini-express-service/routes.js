import express from 'express';
import { showOrder, exportOrder } from './handlers';

const app = express();

app.get('/orders/:id', showOrder);
app.get('/orders/:id/export', exportOrder);
