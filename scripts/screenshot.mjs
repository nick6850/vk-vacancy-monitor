import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import path from "node:path";

const url = "https://team.vk.company/vacancy/?specialty=287";
const outputPath = process.env.SCREENSHOT_PATH || "data/latest-page.png";

await mkdir(path.dirname(outputPath), { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({
  viewport: { width: 1440, height: 1200 },
  deviceScaleFactor: 1,
  locale: "ru-RU",
});

await page.goto(url, { waitUntil: "networkidle", timeout: 60000 });
await page.screenshot({
  path: outputPath,
  fullPage: true,
  animations: "disabled",
});

await browser.close();
console.log(`Saved screenshot to ${outputPath}`);
