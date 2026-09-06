// Run through Playwright MCP's browser_run_code_unsafe(filename=absolute path).
// Start scripts.tools.ui_fixture_server first. All writes belong to temporary DBs.
async (page) => {
  const q = await page.context().newPage();
  const results = [], errors = [], failedResources = [];
  q.on('pageerror', e => errors.push(e.message));
  q.on('response', r => {if(r.status() >= 400) failedResources.push({url:r.url(),status:r.status()});});
  const routes = ['index.html','daily_ops.html','dashboard.html','watchlist.html',
    'symbol.html?code=sh600000','minute_view.html','trading_trainer.html',
    'grid_simulator.html','stock_journal.html?code=sh600000','market_news.html'];
  try {
    for (const [width,height] of [[1440,900],[1024,768],[390,844],[360,800],[844,390]]) {
      await q.setViewportSize({width,height});
      for (const route of routes) {
        await q.goto('http://127.0.0.1:8877/'+route);
        await q.locator('.wb-sidebar').waitFor();
        if(await q.locator('#loading').count()) await q.locator('#loading').waitFor({state:'hidden',timeout:15000});
        await q.waitForTimeout(200);
        const actual = await q.evaluate(()=>document.documentElement.scrollWidth);
        if(actual > width) throw new Error(route+' overflows '+width+': '+actual);
        results.push({route,width,height,scrollWidth:actual});
        if([1440,390].includes(width)) await q.screenshot({path:'output/ui-qa/after-'+route.split('?')[0].replace('.html','')+'-'+width+'.png'});
      }
    }
    await q.setViewportSize({width:390,height:844});
    await q.goto('http://127.0.0.1:8877/dashboard.html');
    await q.locator('.wb-screen-card:visible').first().waitFor();
    const first = await q.locator('.wb-screen-card:visible small').first().innerText();
    const code = first.split(' ')[0];
    await q.locator('.dataTables_filter input:visible').fill(code);
    if(await q.locator('.wb-screen-card:visible').count()!==1) throw new Error('Summary list search not synchronized');
    await q.getByRole('button',{name:'筛选条件',exact:true}).click();
    await q.locator('#fVolume').fill('999');
    await q.keyboard.press('Escape');
    await q.getByRole('button',{name:'筛选条件',exact:true}).click();
    if(await q.locator('#fVolume').inputValue()!=='999') throw new Error('Filter input lost on close');
    await q.keyboard.press('Escape');
    if(errors.length || failedResources.length) throw new Error(JSON.stringify({errors,failedResources}));
    return {layoutChecks:results,summarySearch:true,dialogInputRetention:true,errors,failedResources};
  } finally { await q.close(); }
}
