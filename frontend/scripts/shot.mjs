// Headless screenshots for checking the UI: node scripts/shot.mjs <name> [steps...]
// Steps: wait:<ms>, click:<css>, text:<button text>, eval:<js>, until:<js>, shot:<name>,
//        select:<css>=<value>, mouse:<x>,<y>, mclick:<x>,<y>
import puppeteer from "puppeteer-core";
import { homedir } from "node:os";
import { readdirSync } from "node:fs";

const base = `${homedir()}/.cache/ms-playwright`;
const shell = readdirSync(base).find((d) => d.startsWith("chromium_headless_shell"));
const exe = `${base}/${shell}/chrome-headless-shell-linux64/chrome-headless-shell`;
const url = process.env.URL ?? "http://127.0.0.1:5173/#38.6133951,-90.2593825";
const [name = "shot", ...steps] = process.argv.slice(2);

const browser = await puppeteer.launch({
  executablePath: exe,
  args: ["--no-sandbox", "--disable-dev-shm-usage", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"],
});
const page = await browser.newPage();
await page.setViewport({ width: 1500, height: 920, deviceScaleFactor: 1 });
const errors = [];
page.on("console", (m) => { if (["error", "warning"].includes(m.type())) errors.push(`${m.type()}: ${m.text()}`); });
page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
await page.goto(url, { waitUntil: "domcontentloaded" });
for (const s of steps) {
  const [op, ...rest] = s.split(":");
  const arg = rest.join(":");
  if (op === "wait") await new Promise((r) => setTimeout(r, Number(arg)));
  else if (op === "click") await page.click(arg);
  else if (op === "text") {
    await page.evaluate((t) => {
      const b = [...document.querySelectorAll("button")].find((x) => x.textContent.includes(t));
      if (!b) throw new Error("no button " + t);
      b.click();
    }, arg);
  } else if (op === "eval") await page.evaluate(arg);
  else if (op === "until") await page.waitForFunction(arg, { timeout: 180000 });
  else if (op === "shot") await page.screenshot({ path: `../screenshots/${arg}.png` });
  else if (op === "select") {
    const [css, value] = arg.split("=");
    await page.select(css, value);
  } else if (op === "mouse") {
    const [x, y] = arg.split(",").map(Number);
    await page.mouse.move(x, y, { steps: 4 });
  } else if (op === "mclick") {
    const [x, y] = arg.split(",").map(Number);
    await page.mouse.click(x, y);
  }
}
await page.screenshot({ path: `../screenshots/${name}.png` });
console.log(errors.slice(0, 15).join("\n") || "no console errors");
await browser.close();
