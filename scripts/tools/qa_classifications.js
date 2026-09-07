// Read-only browser regression against already synced example data.
async(page)=>{
 const q=await page.context().newPage(),errors=[];
 const assert=(ok,message)=>{if(!ok)throw new Error(message)};
 q.on('pageerror',e=>errors.push(e.message));
 try{
  for(const [width,height] of [[1440,900],[1024,768],[390,844],[360,800]]){
   await q.setViewportSize({width,height});await q.goto('http://127.0.0.1:8765/symbol.html?code=sh600118');
   await q.locator('#businessTable table').waitFor();
   assert((await q.locator('.classification-tags').nth(1).innerText()).includes('商业航天'),'Concept membership missing');
   assert((await q.locator('#businessTable').innerText()).includes('98.18%'),'Ratio decimal not converted correctly');
   await q.locator('#businessDimension').selectOption('地区');
   assert((await q.locator('#businessTable').innerText()).includes('华北'),'Dimension switching failed');
   await q.locator('#businessPeriod').selectOption('2025-12-31');
   assert(await q.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Page overflow '+width);
   await q.locator('#classificationPanel').scrollIntoViewIfNeeded();
   if(width===390)await q.screenshot({path:'output/ui-qa/classifications-mobile.png'});
  }
  await q.goto('http://127.0.0.1:8765/sector_atlas.html');
  await q.locator('#atlasMemberships a[href*="sh600118"]').waitFor();
  assert((await q.locator('#atlasMemberships').innerText()).includes('中国卫星'),'Member name missing');
  const blocked=await q.request.get('http://127.0.0.1:8765/api/classification?code=sh600118&training_session=invalid');
  assert(blocked.status()===409,'Training context can access current fundamentals');
  assert(!errors.length,JSON.stringify(errors));
  return {widths:[1440,1024,390,360],concepts:true,ratio:true,periodDimension:true,atlasNames:true,trainingBlocked:true,errors};
 }finally{await q.close()}
}
