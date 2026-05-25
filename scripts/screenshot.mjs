import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import path from "node:path";

const url = "https://team.vk.company/vacancy/?specialty=287";
const outputPath = process.env.SCREENSHOT_PATH || "data/latest-page.png";

await mkdir(path.dirname(outputPath), { recursive: true });

const browser = await chromium.launch({ args: ["--disable-dev-shm-usage"] });

try {
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1200 },
    deviceScaleFactor: 1,
    locale: "ru-RU",
  });

  page.setDefaultTimeout(15000);
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 90000 });
  await page.waitForLoadState("load", { timeout: 30000 }).catch(() => {});
  await page.locator("body").waitFor({ timeout: 30000 });
  await page.waitForTimeout(3000);

  await page.screenshot({
    path: outputPath,
    fullPage: true,
    animations: "disabled",
  });

  console.log(`Saved screenshot to ${outputPath}`);
} finally {
  await browser.close();
}
