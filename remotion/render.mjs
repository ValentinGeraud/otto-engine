// Rendu d'un clip avec Remotion (gratuit, local).
//   node render.mjs <props.json> <sortie.mp4>
// Dans props.json, toute chaîne "local:<chemin>" est servie par un petit serveur HTTP local
// (Remotion ne lit pas les fichiers du disque directement).
import { bundle } from "@remotion/bundler";
import { renderMedia, selectComposition, ensureBrowser } from "@remotion/renderer";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const [propsPath, outPath] = process.argv.slice(2);
if (!propsPath || !outPath) {
  console.error("usage: node render.mjs <props.json> <out.mp4>");
  process.exit(2);
}
const props = JSON.parse(fs.readFileSync(propsPath, "utf8"));

// ---- serveur de fichiers locaux ----
const files = [];
const swap = (v) => {
  if (typeof v === "string" && v.startsWith("local:")) {
    files.push(v.slice(6));
    return `__F${files.length - 1}__`;
  }
  if (Array.isArray(v)) return v.map(swap);
  if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, swap(x)]));
  return v;
};
const tmpl = swap(props);
const TYPES = { ".mp4": "video/mp4", ".wav": "audio/wav", ".mp3": "audio/mpeg", ".png": "image/png", ".jpg": "image/jpeg", ".ogg": "audio/ogg", ".m4a": "audio/mp4" };
const server = http.createServer((req, res) => {
  const m = /^\/f\/(\d+)/.exec(req.url || "");
  const file = m && files[Number(m[1])];
  if (!file || !fs.existsSync(file)) { res.writeHead(404); return res.end(); }
  const size = fs.statSync(file).size;
  const type = TYPES[path.extname(file).toLowerCase()] || "application/octet-stream";
  const range = /bytes=(\d*)-(\d*)/.exec(req.headers.range || "");
  const head = { "Content-Type": type, "Accept-Ranges": "bytes", "Access-Control-Allow-Origin": "*" };
  if (range) {
    const a = range[1] ? Number(range[1]) : 0;
    const b = range[2] ? Number(range[2]) : size - 1;
    res.writeHead(206, { ...head, "Content-Range": `bytes ${a}-${b}/${size}`, "Content-Length": b - a + 1 });
    if (req.method === "HEAD") return res.end();
    return fs.createReadStream(file, { start: a, end: b }).pipe(res);
  }
  res.writeHead(200, { ...head, "Content-Length": size });
  if (req.method === "HEAD") return res.end();
  fs.createReadStream(file).pipe(res);
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const base = `http://127.0.0.1:${server.address().port}/f/`;
const inputProps = JSON.parse(JSON.stringify(tmpl).replace(/__F(\d+)__/g, (_, i) => base + i));

// ---- bundle mis en cache (refait seulement si src/ a changé) ----
const buildDir = path.join(HERE, "build");
const stamp = path.join(buildDir, ".stamp");
const newest = Math.max(...fs.readdirSync(path.join(HERE, "src")).map((f) => fs.statSync(path.join(HERE, "src", f)).mtimeMs));
let serveUrl = buildDir;
if (!fs.existsSync(stamp) || Number(fs.readFileSync(stamp, "utf8")) < newest) {
  process.stdout.write("  remotion : préparation du projet…\n");
  serveUrl = await bundle({ entryPoint: path.join(HERE, "src", "index.ts"), outDir: buildDir });
  fs.writeFileSync(stamp, String(newest));
}

await ensureBrowser();
const composition = await selectComposition({ serveUrl, id: "Clip", inputProps });
let last = -1;
const t0 = Date.now();
try {
  await renderMedia({
    composition, serveUrl, codec: "h264", outputLocation: outPath, inputProps,
    frameRange: process.env.RM_FRAMES ? process.env.RM_FRAMES.split("-").map(Number) : null,
    crf: 20, pixelFormat: "yuv420p", x264Preset: "veryfast", jpegQuality: 85, audioCodec: "aac", audioBitrate: "160k",
    concurrency: process.env.RM_CONC ? Number(process.env.RM_CONC) : Math.max(2, Math.min(6, os.cpus().length - 2)),
    chromiumOptions: { gl: "angle" },
    onProgress: ({ progress }) => {
      const p = Math.floor(progress * 10);
      if (p !== last) { last = p; process.stdout.write(`\r  remotion : ${p * 10}%`); }
    },
  });
} finally {
  server.close();
}
process.stdout.write(`\r  remotion : rendu en ${((Date.now() - t0) / 1000).toFixed(1)} s\n`);
process.exit(0);
