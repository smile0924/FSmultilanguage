// Local server for the language checker: serves ./public and proxies the game servers,
// which do not send CORS headers, so the browser can read their pages.
import http from 'node:http';
import os from 'node:os';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const PORT = Number(process.env.PORT) || 5178;
const HOST = process.env.HOST || '127.0.0.1';
const ALLOWED_HOSTS = (process.env.ALLOW_HOSTS || 'object1.net')
  .split(',').map((s) => s.trim().toLowerCase()).filter(Boolean);
const PUBLIC_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), 'public');
const MAX_BYTES = 30 * 1024 * 1024;

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
};

function sendJson(res, status, body) {
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' });
  res.end(JSON.stringify(body));
}

function isAllowed(url) {
  return (url.protocol === 'https:' || url.protocol === 'http:')
    && ALLOWED_HOSTS.some((h) => url.hostname === h || url.hostname.endsWith(`.${h}`));
}

async function proxy(res, target) {
  let url;
  try {
    url = new URL(target);
  } catch {
    return sendJson(res, 400, { error: '網址格式不正確' });
  }
  for (let hop = 0; hop < 5; hop++) {
    if (!isAllowed(url)) return sendJson(res, 403, { error: `不允許的網域：${url.hostname}（可用 ALLOW_HOSTS 設定）` });
    let upstream;
    try {
      upstream = await fetch(url, {
        redirect: 'manual',
        headers: { 'user-agent': 'FS-multilanguage-checker/1.0' },
        signal: AbortSignal.timeout(20000),
      });
    } catch (err) {
      return sendJson(res, 502, { error: `無法連線：${err.cause?.code || err.message}` });
    }
    const location = upstream.headers.get('location');
    if (upstream.status >= 300 && upstream.status < 400 && location) {
      url = new URL(location, url);
      continue;
    }
    const body = Buffer.from(await upstream.arrayBuffer());
    if (body.length > MAX_BYTES) return sendJson(res, 413, { error: '檔案太大' });
    res.writeHead(upstream.status, {
      'content-type': upstream.headers.get('content-type') || 'application/octet-stream',
      'x-final-url': url.href,
      'cache-control': 'no-store',
    });
    return res.end(body);
  }
  return sendJson(res, 508, { error: '轉址次數過多' });
}

async function serveStatic(res, pathname) {
  const rel = decodeURIComponent(pathname === '/' ? '/index.html' : pathname);
  const file = path.normalize(path.join(PUBLIC_DIR, rel));
  if (!file.startsWith(PUBLIC_DIR + path.sep)) return sendJson(res, 403, { error: 'forbidden' });
  try {
    const data = await readFile(file);
    res.writeHead(200, { 'content-type': MIME[path.extname(file).toLowerCase()] || 'application/octet-stream', 'cache-control': 'no-cache' });
    res.end(data);
  } catch {
    sendJson(res, 404, { error: 'not found' });
  }
}

const server = http.createServer(async (req, res) => {
  const { pathname, searchParams } = new URL(req.url, 'http://localhost');
  try {
    if (pathname === '/api/proxy') return await proxy(res, searchParams.get('url') || '');
    if (req.method !== 'GET' && req.method !== 'HEAD') return sendJson(res, 405, { error: 'method not allowed' });
    return await serveStatic(res, pathname);
  } catch (err) {
    sendJson(res, 500, { error: String(err.message || err) });
  }
});

/** This machine's addresses on the local network, for colleagues to type in. */
function lanAddresses() {
  return Object.values(os.networkInterfaces()).flat()
    .filter((a) => a && a.family === 'IPv4' && !a.internal)
    .map((a) => a.address);
}

server.listen(PORT, HOST, () => {
  const shared = HOST === '0.0.0.0';
  console.log(`多語系比對工具已啟動：http://${shared ? 'localhost' : HOST}:${PORT}`);
  if (shared) {
    console.log('\n同事請用瀏覽器開下面其中一個網址（要和這台電腦在同一個網路）：');
    for (const ip of lanAddresses()) console.log(`  http://${ip}:${PORT}`);
    console.log('\n這個視窗關掉，網站就會停止。');
  }
  console.log(`\n代理允許的網域：${ALLOWED_HOSTS.join(', ')}`);
});
