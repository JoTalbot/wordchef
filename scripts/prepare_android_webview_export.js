#!/usr/bin/env node

const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..", "frontend", "out");
const source = path.join(root, "_next");
const target = path.join(root, "next");

if (!fs.existsSync(source)) {
  throw new Error("Next.js export is missing frontend/out/_next");
}

fs.rmSync(target, { recursive: true, force: true });
fs.cpSync(source, target, { recursive: true });

let rewritten = 0;
function walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(full);
    else if (/\.(html|js|css|json|map)$/i.test(entry.name)) {
      const before = fs.readFileSync(full, "utf8");
      const after = before.replace(/\/_next\//g, "/next/");
      if (after !== before) {
        fs.writeFileSync(full, after);
        rewritten++;
      }
    }
  }
}

walk(root);
console.log(`Android WebView export prepared: copied _next -> next; rewrote ${rewritten} files`);
