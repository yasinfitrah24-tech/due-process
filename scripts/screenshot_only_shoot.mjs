// SCREENSHOT ONLY. Takes docs/screens/ from local copies of the app, at 375x812 and 1280x800. Not used by the app.
//
// Needs Edge or Chrome headless on port 9333, and three copies of the app, none of them real:
//   8765 a folder made by screenshot_only_stage.py, 8766 an untouched copy of demo data, 8767 an empty folder.
//
//   node scripts/screenshot_only_shoot.mjs <copy>/tokens.json docs/screens [all|errors|queue-full|flood]
import { readFileSync, writeFileSync } from "node:fs";

const [tokensFile, out, group = "all"] = process.argv.slice(2);
const T = JSON.parse(readFileSync(tokensFile, "utf8"));
const A = "http://localhost:8765", R = "http://localhost:8766", E = "http://localhost:8767";
const post = (path, body, base = A) => ({ post: path, body, base });
const groups = {
  all: [
    ["front", R + "/"], ["front-empty", E + "/"],
    ["rejected-report", post("/reports", "url=http://unknown.test/&claim=x", R)],
    ["rejected-test-url", post("/test-url", "url=ftp://x.test/", R)],
    ...[["case-proven", 28], ["case-containment", 37], ["case-suspicious", 41], ["case-not-proven", 34], ["case-running", 45],
      ["case-decided-suspend", 2], ["case-decided-warn", 5], ["case-decided-close", 39], ["case-template", 6],
      ["case-visit-failed", 42], ["case-cost-limit", 43], ["case-claim-rejected", 44], ["case-appeal-ready", 17],
      ["case-appeal-running", 46], ["case-appeal-reinstated", 47], ["case-appeal-visit-failed", 48], ["case-appeal-kept", 49],
      ["case-teardown-failed", 53]].map(([name, id]) => [name, `${A}/cases/${id}`]),
    ["test-any-url", `${A}/cases/${T.test}`], ["twin-case", `${A}/cases/${T.twin}`],
    ["compare", `${A}/compare`], ["compare-result", `${A}/compare/${T.comparison}`],
    ["compare-running", `${A}/compare/${T.running_comparison}`],
    ["customer-appeal-ready", `${A}/appeal/${T.appeal}`], ["customer-open", `${A}/appeal/staged-50`],
    ["customer-running", `${A}/appeal/staged-46`], ["customer-reinstated", `${A}/appeal/staged-47`],
    ["customer-kept", `${A}/appeal/staged-49`], ["customer-limit", `${A}/appeal/staged-51`],
    ["customer-warn", `${A}/appeal/staged-52`], ["customer-visit-failed", `${A}/appeal/staged-48`],
  ],
  errors: [["error-404", `${A}/cases/99999`], ["error-403", post("/cases/34/actions", "action=Suspend")],
    ["error-409", post("/cases/2/actions", "action=Suspend")]],
  "queue-full": [["error-503", post("/reports", "url=http://tailspin-offers.test/start&claim=x")]],
  flood: [["error-429", { ...post("/reports", "url=http://unknown.test/&claim=x"), flood: true }]],
};
const FULL = { 375: ["front", "rejected-report", "case-containment", "compare-result", "customer-appeal-ready"],
  1280: ["front", "rejected-report", "case-containment", "compare-result", "customer-appeal-ready", "case-appeal-ready"] };

const target = await (await fetch("http://127.0.0.1:9333/json/new?about:blank", { method: "PUT" })).json();
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((r) => (ws.onopen = r));
let id = 0, problems = [];
const waiting = new Map();
ws.onmessage = (e) => {
  const m = JSON.parse(e.data);
  if (m.id && waiting.has(m.id)) { waiting.get(m.id)(m); waiting.delete(m.id); }
  if (m.method === "Runtime.exceptionThrown") problems.push("exception: " + m.params.exceptionDetails.text);
  if (m.method === "Log.entryAdded" && m.params.entry.level === "error" && !m.params.entry.text.startsWith("Failed to load resource")) problems.push(m.params.entry.text);
};
const send = (method, params = {}) => new Promise((r) => { waiting.set(++id, r); ws.send(JSON.stringify({ id, method, params })); });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
await send("Page.enable"); await send("Runtime.enable"); await send("Log.enable");

async function go(page) {
  if (typeof page === "string") {
    await send("Page.navigate", { url: page });
    await sleep(1800);
    return;
  }
  await send("Page.navigate", { url: `${page.base}/compare` });
  await sleep(500);
  const args = `${JSON.stringify(page.post)},${JSON.stringify(page.body)}`;
  if (page.flood) await send("Runtime.evaluate", { awaitPromise: true, expression: `(async(u,b)=>{for(let i=0;i<12;i++)await fetch(u,{method:'POST',body:new URLSearchParams(b)})})(${args})` });
  await send("Runtime.evaluate", { expression: `((u,b)=>{const f=document.createElement('form');f.method='post';f.action=u;new URLSearchParams(b).forEach((v,k)=>{const i=document.createElement('input');i.name=k;i.value=v;f.appendChild(i)});document.body.appendChild(f);f.submit()})(${args})` });
  await sleep(1500);
}

const measure = `(()=>{const y=e=>e&&e.offsetParent?Math.round(e.getBoundingClientRect().top+scrollY):undefined;
return {w:innerWidth,sw:document.documentElement.scrollWidth,H:document.documentElement.scrollHeight,level:y(document.querySelector('.badge')),action:y(document.querySelector('.actions button')),queue:y(document.querySelector('#queue'))}})()`;
for (const [name, page] of groups[group]) {
  for (const [w, h] of [[375, 812], [1280, 800]]) {
    await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: w < 700 });
    problems = [];
    await go(page);
    const m = (await send("Runtime.evaluate", { returnByValue: true, expression: measure })).result.result.value;
    const shot = await send("Page.captureScreenshot", { format: "png" });
    writeFileSync(`${out}/${name}-${w}.png`, Buffer.from(shot.result.data, "base64"));
    if (FULL[w].includes(name)) {
      const full = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true, clip: { x: 0, y: 0, width: w, height: m.H, scale: 1 } });
      writeFileSync(`${out}/${name}-${w}-full.png`, Buffer.from(full.result.data, "base64"));
    }
    console.log(`${name}-${w}`, m.sw > m.w ? `OVERFLOW ${m.sw}` : "", "H", m.H, m.level ?? "", m.action ?? "", m.queue ?? "", problems.join(" | "));
  }
}
ws.close();
