const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const PARSER = path.join(__dirname, '..', 'parse.js');

function indexRepo(files) {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'lumen-js-outbound-'));
    for (const [name, content] of Object.entries(files)) {
        const full = path.join(dir, name);
        fs.mkdirSync(path.dirname(full), { recursive: true });
        fs.writeFileSync(full, content);
    }
    const run = spawnSync('node', [PARSER, dir, '--backend', 'json'], { encoding: 'utf-8', maxBuffer: 64 * 1024 * 1024 });
    fs.rmSync(dir, { recursive: true, force: true });
    assert.strictEqual(run.status, 0, run.stderr);
    const graph = JSON.parse(run.stdout);
    const apis = graph.nodes.filter(n => n.type === 'EXTERNAL_API');
    const edges = graph.relationships.filter(r => r.type === 'CALLS_EXTERNAL');
    return { graph, apis, edges, names: new Set(apis.map(a => a.name)) };
}

test('detects axios, fetch and node http with env/const/cross-file resolution', () => {
    const { names, edges, apis } = indexRepo({
        '.env': 'PAYMENTS_URL=https://pay.example.com/v2\nSECRET_KEY=abc123\nAPI_TOKEN=https://user:pw@leak.example.com\n',
        'src/config.js': "export const INVENTORY_BASE = 'https://inv.example.com/api';\nexport default { shipping: 'https://ship.example.com' };\n",
        'src/clients.js': `
import axios from 'axios';
import { INVENTORY_BASE } from './config';
import cfg from './config';

const crm = axios.create({ baseURL: 'https://crm.example.com/v1' });

export async function loadStock(sku) {
  return axios.get(\`\${INVENTORY_BASE}/stock/\${sku}\`);
}
export async function createLead(body) {
  return crm.post('/leads', body);
}
export function pay(order) {
  return fetch(\`\${process.env.PAYMENTS_URL}/charge\`, { method: 'POST', body: JSON.stringify(order) });
}
export function track(id) {
  return fetch(cfg.shipping + '/track/' + encodeURIComponent(id));
}
export function legacy() {
  return axios({ method: 'put', url: 'https://legacy.example.com/sync' });
}
export function ping() {
  const https = require('https');
  return https.get({ hostname: 'status.example.com', path: '/ping' });
}
export function sameOrigin() {
  return fetch('/api/internal');
}
export function unknown(url) {
  return fetch(url);
}
`,
    });
    for (const expected of [
        'GET https://inv.example.com/api/stock/{sku}',
        'POST https://crm.example.com/v1/leads',
        'POST https://pay.example.com/v2/charge',
        'GET https://ship.example.com/track/{id}',
        'PUT https://legacy.example.com/sync',
    ]) {
        assert.ok(names.has(expected), `missing ${expected}; got ${[...names]}`);
    }
    assert.ok(![...names].some(n => n.includes('/api/internal')), 'relative same-origin call must be skipped');
    assert.ok(![...names].some(n => n.includes('leak.example.com') || n.includes('abc123')), 'credential env values must not leak');
    assert.ok(![...names].some(n => n === 'GET {url}'), 'bare placeholder urls are skipped');
    const crm = apis.find(a => a.name === 'POST https://crm.example.com/v1/leads');
    assert.strictEqual(crm.properties.host, 'crm.example.com');
    assert.strictEqual(crm.properties.client, 'axios');
    assert.ok(edges.some(e => e.sourceId === 'function:src.clients.createLead'), 'edge originates at the calling function');
});

test('detects NestJS HttpService and Angular HttpClient by injected type', () => {
    const { names } = indexRepo({
        'src/billing.service.ts': `
import { HttpService } from '@nestjs/axios';
export class BillingService {
  private readonly base = 'https://billing.example.com';
  constructor(private readonly httpService: HttpService) {}
  invoice(id: string) {
    return this.httpService.get(\`\${this.base}/invoices/\${id}\`);
  }
}
`,
        'src/user.service.ts': `
import { HttpClient } from '@angular/common/http';
export class UserService {
  constructor(private http: HttpClient) {}
  list() { return this.http.get<User[]>('https://users.example.com/users'); }
  remove(id: number) { return this.http.delete(\`https://users.example.com/users/\${id}\`); }
}
`,
    });
    assert.ok(names.has('GET https://billing.example.com/invoices/{id}'), [...names].join('\n'));
    assert.ok(names.has('GET https://users.example.com/users'), [...names].join('\n'));
    assert.ok(names.has('DELETE https://users.example.com/users/{id}'), [...names].join('\n'));
});

test('does not treat unrelated .get() calls or supertest as outbound', () => {
    const { apis } = indexRepo({
        'src/app.js': `
const cache = new Map();
const request = require('supertest');
export function a(k) { return cache.get('https://not-a-call.example.com'); }
export function b(app) { return request(app).get('https://also-not.example.com'); }
`,
        'src/app.spec.js': "import axios from 'axios';\naxios.get('https://test-only.example.com');\n",
    });
    assert.deepStrictEqual(apis.map(a => a.name), []);
});
