import { expect, test, type Page } from "@playwright/test";

// Journeys from the mocks, run against the fake hub. Each test saves a screenshot per
// theme to e2e/screenshots/, which CI uploads for review.

async function shoot(page: Page, name: string) {
  for (const scheme of ["dark", "light"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    await page.screenshot({ path: `e2e/screenshots/${name}-${scheme}.png` });
  }
  await page.emulateMedia({ colorScheme: "dark" });
}

async function open(page: Page, scenario: string, setupDone = true) {
  await page.addInitScript((done) => {
    if (done) localStorage.setItem("vibe2knob.setupDone", "true");
  }, setupDone);
  await page.goto(`/?fake=${scenario}`);
}

test("ask a vibe: steps stream in with knobs and a Listen prompt", async ({ page }) => {
  await open(page, "empty");
  await expect(page.getByText("What should it feel like?")).toBeVisible();
  await expect(page.getByRole("button", { name: /Warm Pad, following your selection/ })).toBeVisible();
  await shoot(page, "01-empty");

  await page.getByLabel("Describe the vibe").fill("make it wider");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.locator(".user-msg")).toHaveText("make it wider");
  await expect(page.getByRole("listitem").filter({ hasText: "Chorus-Ensemble" })).toBeVisible();
  await expect(page.getByText("Listen", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Pad width", exact: true })).toBeVisible(); // topic title
  await page.getByRole("button", { name: /Step 1: Utility › Width\. Mark done/ }).click();
  await expect(page.getByRole("button", { name: /Step 1: Utility › Width\. Done/ })).toBeVisible();
  await shoot(page, "02-ask");
});

test("saved chats reload their history and switch by tab", async ({ page }) => {
  await open(page, "chat");
  await expect(page.getByText("Spread the pad around")).toBeVisible();
  await page.getByRole("button", { name: "Low-end mud", exact: true }).click();
  await expect(page.getByText("Three things fight under 200 Hz")).toBeVisible();
  await expect(page.locator(".step-track").first()).toContainText("Warm Pad");
  await shoot(page, "03-multi-track");
});

test("refine with a quick reply", async ({ page }) => {
  await open(page, "chat");
  await page.getByRole("button", { name: "too much" }).click();
  await expect(page.getByText("The wash comes from the chorus")).toBeVisible();
  await expect(page.getByText("0.5–1 Hz")).toBeVisible();
  await shoot(page, "04-refine");
});

test("@ pins a track and the picker lists the chain", async ({ page }) => {
  await open(page, "chat");
  await page.getByRole("button", { name: "@ Add track" }).click();
  await expect(page.getByRole("option", { name: /Lead Vox/ })).toContainText("EQ Eight → Compressor → Reverb");
  await shoot(page, "05-picker");
  await page.getByRole("option", { name: /Lead Vox/ }).click();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "Remove Lead Vox" })).toBeVisible();
  await expect(page.getByText(/Following Warm Pad · 1 pinned/)).toBeVisible();
});

test("selection changes in Live move the follow chip", async ({ page }) => {
  await open(page, "chat");
  await expect(page.getByRole("button", { name: /Warm Pad, following/ })).toBeVisible();
  await page.evaluate(() => window.__fakeHub!.select(1));
  await expect(page.getByRole("button", { name: /Sub Bass, following/ })).toBeVisible();
});

test("a general chat has no tracks", async ({ page }) => {
  await open(page, "empty");
  await page.getByRole("button", { name: /following your selection in Live/ }).click();
  await expect(page.getByText("General · no tracks")).toBeVisible();
  await page.getByLabel("Describe the vibe").fill("Guitar Rig doesn't show up in Live");
  await page.keyboard.press("Enter");
  await expect(page.getByText("only lists plugins from the formats")).toBeVisible();
  // The answer's markdown renders, and no raw markers show.
  await expect(page.locator(".prose ol li")).toHaveCount(2);
  await expect(page.locator(".prose strong").last()).toHaveText("Rescan");
  await expect(page.locator(".prose code")).toHaveText("Option");
  await expect(page.locator(".prose")).not.toContainText("**");
  await expect(page.locator(".step")).toHaveCount(0);
  await shoot(page, "06-general");
});

test("Live offline offers example tracks", async ({ page }) => {
  await open(page, "offline");
  await expect(page.getByText("I can't hear your session yet")).toBeVisible();
  await expect(page.getByLabel("Describe the vibe")).toBeDisabled();
  await shoot(page, "07-offline");
  await page.getByRole("button", { name: "Sub Bass" }).first().click();
  await expect(page.getByRole("button", { name: /Sub Bass, following/ })).toBeVisible();
  await expect(page.getByLabel("Describe the vibe")).toBeEnabled();
});

test("a failed turn shows its error inside the chat", async ({ page }) => {
  await open(page, "empty");
  await page.getByLabel("Describe the vibe").fill("this will fail");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("alert")).toContainText("didn't answer");
});

test("first run walks through the three steps", async ({ page }) => {
  await open(page, "firstrun", false);
  await expect(page.getByText("Tell it the vibe. You turn the knobs.")).toBeVisible();
  await shoot(page, "00-setup");
  await page.getByLabel("Anthropic API key").fill("sk-ant-test");
  await page.getByRole("button", { name: "Connect" }).click();
  await expect(page.getByRole("button", { name: "Start" })).toBeVisible();
  await page.getByRole("button", { name: "Start" }).click();
  await expect(page.getByText("What should it feel like?")).toBeVisible();
});

test("settings show the key state, never the key", async ({ page }) => {
  await open(page, "chat");
  await page.getByRole("button", { name: "Settings" }).click();
  await expect(page.getByLabel("Anthropic API key")).toHaveValue("");
  await expect(page.getByLabel("Anthropic API key")).toHaveAttribute("placeholder", /Saved/);
  await page.getByRole("button", { name: "Test" }).click();
  await expect(page.getByText("Key works")).toBeVisible();
  await shoot(page, "08-settings");
  await page.getByRole("button", { name: "Back to chats" }).click();
  await expect(page.getByText("Spread the pad around")).toBeVisible();
});

test("a hub restart reconnects and reloads the chat", async ({ page }) => {
  await open(page, "chat");
  await expect(page.getByText("Spread the pad around")).toBeVisible();
  await page.evaluate(() => window.__fakeHub!.drop());
  await expect(page.getByText("Spread the pad around")).toBeVisible();
  await expect(page.locator(".status").first()).toContainText("Live");
});

test("the panel fills a wide window and moves tabs into a sidebar", async ({ page }) => {
  await open(page, "chat");
  const tabs = page.getByRole("navigation", { name: "Chats" });
  const isSidebar = async () => {
    const box = (await tabs.boundingBox())!;
    return box.height > box.width;
  };

  await page.setViewportSize({ width: 800, height: 860 });
  await expect(page.getByText("Spread the pad around")).toBeVisible();
  expect(await isSidebar()).toBe(false);
  await shoot(page, "11-medium");

  await page.setViewportSize({ width: 1400, height: 860 });
  await expect.poll(isSidebar).toBe(true);
  await expect(page.getByRole("button", { name: "New chat" })).toContainText("New chat");
  const composer = (await page.locator(".composer .input-row").boundingBox())!;
  expect(composer.width).toBeLessThanOrEqual(760); // a readable column, not the full width
  await page.getByRole("button", { name: "Low-end mud", exact: true }).click();
  await expect(page.getByText("Three things fight under 200 Hz")).toBeVisible();
  await shoot(page, "12-wide");
});
