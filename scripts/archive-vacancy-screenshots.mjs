import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import path from "node:path";

const listUrl = "https://team.vk.company/vacancy/?specialty=287";
const outputDir = process.env.VACANCY_SCREENSHOT_DIR || "data/vacancy-screenshots";

function parseVacancies(html) {
  const match = html.match(/<script id="__NEXT_DATA__" type="application\/json">(.*?)<\/script>/s);
  if (!match) {
    throw new Error("Could not find __NEXT_DATA__ in vacancy list page");
  }

  const data = JSON.parse(match[1].replaceAll("&quot;", "\""));
  const vacancies = data?.props?.pageProps?.initialVacancies;
  if (!Array.isArray(vacancies)) {
    throw new Error("Could not find initialVacancies in __NEXT_DATA__");
  }
  return vacancies.map((vacancy) => ({
    id: String(vacancy.id),
    url: `https://team.vk.company/vacancy/${vacancy.id}/`,
  }));
}

await mkdir(outputDir, { recursive: true });

const browser = await chromium.launch({ args: ["--disable-dev-shm-usage"] });

try {
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1200 },
    deviceScaleFactor: 1,
    locale: "ru-RU",
  });

  page.setDefaultTimeout(15000);
  await page.goto(listUrl, { waitUntil: "domcontentloaded", timeout: 90000 });
  await page.waitForLoadState("load", { timeout: 30000 }).catch(() => {});
  const vacancies = parseVacancies(await page.content());

  for (const vacancy of vacancies) {
    const outputPath = path.join(outputDir, `${vacancy.id}.png`);
    await page.goto(vacancy.url, { waitUntil: "domcontentloaded", timeout: 90000 });
    await page.waitForLoadState("load", { timeout: 30000 }).catch(() => {});
    await page.locator("body").waitFor({ timeout: 30000 });
    await page.waitForTimeout(1500);
    await page.screenshot({
      path: outputPath,
      fullPage: true,
      animations: "disabled",
    });
    console.log(`Saved vacancy screenshot ${vacancy.id} to ${outputPath}`);
  }
} finally {
  await browser.close();
}
