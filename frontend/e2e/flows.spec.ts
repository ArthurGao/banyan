import { expect, test, type Page } from '@playwright/test'

// The main flows of design §12 on the three viewports, against the real backend with fakes
// (e2e/serve.sh: FakeLLM, FakeSteward, no web). English UI so the selectors read the en strings.
const TOKEN = 'e2e-token-not-a-secret-0123'

test.beforeEach(async ({ page }) => {
  await page.addInitScript((token) => {
    localStorage.setItem('banyan.token', token)
    localStorage.setItem('banyan.lang', 'en')
  }, TOKEN)
})

async function noHorizontalScroll(page: Page) {
  // On failure, name the elements that stick out past the right edge.
  const wide = await page.evaluate(() =>
    [...document.querySelectorAll('body *')]
      .filter((el) => el.getBoundingClientRect().right > window.innerWidth + 1)
      .slice(0, 5)
      .map((el) => `${el.tagName.toLowerCase()}.${(el.getAttribute('class') ?? '').slice(0, 60)}`),
  )
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  expect(overflow, wide.join('\n')).toBeLessThanOrEqual(0)
  // Nor up and down: panes scroll on their own, so the composer sticks to the bottom of its pane.
  const tall = await page.evaluate(() => document.documentElement.scrollHeight - window.innerHeight)
  expect(tall, 'the page itself scrolls vertically').toBeLessThanOrEqual(0)
}

async function createTopic(page: Page, title: string) {
  await page.goto('/')
  await page.getByRole('button', { name: 'New topic' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByRole('textbox', { name: 'What do you want to learn?' }).fill(title)
  await dialog.getByRole('button', { name: 'Next' }).click()
  await dialog.getByRole('button', { name: 'Skip' }).click()
  await dialog.getByRole('button', { name: 'Some background' }).click()
  await dialog.getByRole('button', { name: 'English' }).click()
  await dialog.getByRole('button', { name: 'Generate the outline' }).click()
  // The outline streams in, then the editor shows it.
  await expect(page.getByRole('button', { name: 'Confirm the outline' })).toBeVisible({ timeout: 20_000 })
  await expect(page.locator('main [role=tree] [role=treeitem]')).toHaveCount(9)
  return page.url()
}

async function confirm(page: Page, mode: RegExp) {
  await page.getByRole('button', { name: 'Confirm the outline' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByRole('radio', { name: mode }).click()
  await dialog.getByRole('button', { name: 'Confirm the outline' }).click()
}

test('topic → outline → explanation → branch → feedback → export', async ({ page }, info) => {
  const topicUrl = await createTopic(page, `Kubernetes networking (${info.project.name})`)
  await confirm(page, /Prefetch/)

  // The first node: its explanation streams on open.
  await page.getByRole('link', { name: /^1 Part 1/ }).first().click()
  const explanation = page.getByRole('region', { name: 'Explanation' })
  await expect(explanation.getByRole('button', { name: 'Helpful' })).toBeVisible({ timeout: 20_000 })
  await expect(explanation.locator('[data-source]').first()).toHaveAttribute('data-source', /kb|llm|web|mixed/)
  await noHorizontalScroll(page)

  // Ask here: a new branch, the answer streams with its source.
  await page.getByRole('textbox', { name: 'Ask here' }).fill('How is a ClusterIP assigned?')
  await page.getByRole('button', { name: 'Ask here' }).click()
  await expect(page).toHaveURL(/\/branches\//)
  const answer = page.locator('article[data-message]').last()
  await expect(answer.getByRole('button', { name: 'Wrong' })).toBeVisible({ timeout: 20_000 })
  const firstId = await answer.getAttribute('data-message')
  await expect(answer.locator('[aria-label^="Source:"]')).toBeVisible()

  // Wrong → re-answer: the old answer folds away, a new one replaces it.
  await answer.getByRole('button', { name: 'Wrong' }).click()
  await page.getByRole('dialog').getByRole('textbox').fill('It skipped the Service CIDR.')
  await page.getByRole('button', { name: 'Send and re-answer' }).click()
  await expect(page.getByText('Replaced by a re-answer')).toBeVisible({ timeout: 20_000 })
  const current = page.locator('ol > li > article[data-message]').last()
  await expect(current).not.toHaveAttribute('data-message', firstId ?? '')
  await noHorizontalScroll(page)
  await page.screenshot({ path: `test-results/branch-${info.project.name}.png`, fullPage: true })

  // Export: built by the backend, previewed in a sandboxed frame that fetches nothing.
  await page.goto(topicUrl)
  await expect(page.getByRole('button', { name: 'Build HTML' })).toBeVisible()
  const external: string[] = []
  page.on('request', (r) => {
    if (!r.url().startsWith('http://127.0.0.1:8812/v1/')) external.push(r.url())
  })
  await page.getByRole('button', { name: 'Build HTML' }).click()
  const frame = page.frameLocator('iframe[title="Preview"]')
  await expect(frame.getByText('Part 1: fake outline node').first()).toBeVisible({ timeout: 15_000 })
  await expect(page.getByRole('link', { name: 'Download' })).toHaveAttribute('download', /\.html$/)
  expect(external).toEqual([])
  await noHorizontalScroll(page)
  await page.screenshot({ path: `test-results/export-${info.project.name}.png`, fullPage: true })
})

test('generate all with progress, then quick ask into a new node', async ({ page }, info) => {
  const topicUrl = await createTopic(page, `Generate all (${info.project.name})`)
  await confirm(page, /Generate all/)
  const estimate = page.getByRole('dialog')
  await expect(estimate.getByText(/9 nodes to generate/)).toBeVisible()
  await estimate.getByRole('button', { name: 'Start' }).click()
  // The progress bar shows while the run works, and goes away once it is done (no failed nodes).
  await expect(page.locator('[data-run-status]')).toBeVisible()
  await expect(page.locator('[data-run-status]')).toHaveCount(0, { timeout: 30_000 })
  const topicId = topicUrl.split('/topics/')[1]
  const nodes = await page.evaluate(
    async ([id, token]) => {
      // A failed request names itself (one unexplained 500 was seen once in this step, 2026-11-13).
      const get = async (path: string): Promise<unknown> => {
        const r = await fetch(path, { headers: { Authorization: `Bearer ${token}` } })
        if (!r.ok) throw new Error(`${path} → ${r.status}: ${(await r.text()).slice(0, 300)}`)
        return r.json()
      }
      const body = (await get(`/v1/topics/${id}`)) as { nodes: { id: string }[] }
      return Promise.all(body.nodes.map(async (n) => ((await get(`/v1/nodes/${n.id}`)) as { explanation_status: string }).explanation_status))
    },
    [topicId, TOKEN],
  )
  expect(nodes).toEqual(Array(9).fill('done'))

  // Quick ask: the fake classifier is not confident and suggests a node; add it and ask.
  await page.getByRole('button', { name: 'Quick ask' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByRole('textbox', { name: 'Quick ask' }).fill('NodePort or LoadBalancer?')
  await dialog.getByRole('button', { name: 'Ask', exact: true }).click()
  await dialog.getByRole('button', { name: 'Add the node and ask' }).click()
  await expect(dialog.getByRole('button', { name: 'Open the branch' })).toBeVisible({ timeout: 20_000 })
  await dialog.getByRole('button', { name: 'Open the branch' }).click()
  await expect(page).toHaveURL(/\/nodes\/.+\/branches\//)
  await expect(page.getByRole('heading', { name: 'NodePort or LoadBalancer?' })).toBeVisible()
  await noHorizontalScroll(page)
  await page.screenshot({ path: `test-results/quick-ask-${info.project.name}.png`, fullPage: true })
})
