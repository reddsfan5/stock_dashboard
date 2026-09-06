// Read-only regression: run with Playwright MCP browser_run_code_unsafe(filename=...).
async (page) => {
  const q = await page.context().newPage();
  const errors = [], results = [];
  const assert = (value, message) => { if (!value) throw new Error(message); };
  q.on('pageerror', e => errors.push(e.message));
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  await q.route('**/assets/workbench.js', async route => { await gate; await route.continue(); });
  try {
    await q.setViewportSize({width:1440,height:900});
    await q.goto('http://127.0.0.1:8765/dashboard.html', {waitUntil:'commit'});
    await q.locator('#screen-loading').waitFor({state:'visible'});
    await q.waitForFunction(()=>document.querySelector('.content'));
    assert(await q.locator('.content').evaluate(el=>getComputedStyle(el).visibility==='hidden'), 'Raw table exposed while enhancements load');
    await q.screenshot({path:'output/ui-qa/screen-loading-desktop.png'});
    release();
    await q.waitForFunction(()=>!document.documentElement.classList.contains('screen-pending'));
    assert(await q.evaluate(()=>$.fn.dataTable.tables().length===1), 'Inactive strategies eagerly initialized');
    assert(await q.evaluate(()=>performance.getEntriesByType('resource').every(r=>!r.name.includes('datatables-zh'))), 'Language request blocks initialization');
    await q.locator('.tabs button').nth(2).click();
    assert(await q.evaluate(()=>$.fn.dataTable.tables().length===2), 'Strategy is not initialized on demand');
    await q.locator('.tabs button').first().click();
    assert(await q.evaluate(()=>$.fn.dataTable.tables().length===2), 'Strategy initialized twice');
    for (const [width,height] of [[1440,900],[1024,768],[390,844],[360,800],[844,390]]) {
      await q.setViewportSize({width,height});
      assert(await q.evaluate(()=>document.documentElement.scrollWidth<=innerWidth), 'Overflow at '+width);
      results.push({width,height,overflow:false});
    }
    await q.setViewportSize({width:390,height:844});
    const first = await q.locator('.wb-screen-card:visible small').first().innerText();
    const search = q.locator('.dataTables_filter input:visible');
    await search.fill(first.split(' ')[0]);
    assert(await q.locator('.wb-screen-card:visible').count()===1, 'Search and cards diverge');
    await search.fill('');
    await q.getByRole('tab',{name:'风险指标',exact:true}).click();
    assert((await q.locator('.wb-screen-card:visible').first().innerText()).includes('ATR14'), 'Metric group did not update cards');
    await q.locator('.paginate_button.next:visible').click();
    assert(await q.evaluate(()=>$('#tbl-'+currentTabId).DataTable().page.info().page===1), 'Pagination failed');
    assert((await q.locator('.wb-screen-card:visible small').first().innerText())!==first, 'Cards did not follow pagination');
    await q.getByRole('button',{name:'筛选条件',exact:true}).click();
    await q.locator('#fVolume').fill('999999');
    await q.locator('#applyFilters').click();
    assert(await q.locator('.wb-screen-cards:visible').innerText()==='没有符合条件的标的，请调整筛选。', 'Filter empty state failed');
    await q.getByRole('button',{name:'筛选条件',exact:true}).click();
    await q.locator('#resetFilters').click();
    await q.keyboard.press('Escape');
    await q.screenshot({path:'output/ui-qa/screen-ready-mobile.png'});
    await q.setViewportSize({width:1440,height:900});
    await q.screenshot({path:'output/ui-qa/screen-ready-desktop.png'});
    assert(errors.length===0, JSON.stringify(errors));
    return {slowLoadRawContentHidden:true,lazyInitialization:true,searchMetricsPaginationFilters:true,layouts:results,errors};
  } finally { release(); await q.close(); }
}
