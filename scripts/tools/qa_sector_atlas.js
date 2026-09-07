// Read-only Playwright MCP regression for the illustrated sector guide.
async(page)=>{
 const q=await page.context().newPage(),errors=[],failed=[];
 const assert=(value,message)=>{if(!value)throw new Error(message)};
 q.on('pageerror',e=>errors.push(e.message));q.on('response',r=>{if(r.status()>=400)failed.push(r.url())});
 try{
  for(const [width,height] of [[1440,900],[1024,768],[390,844],[360,800]]){
   await q.setViewportSize({width,height});await q.goto('http://127.0.0.1:8765/sector_atlas.html');
   await q.locator('.wb-sidebar').waitFor();
   assert(await q.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Page overflow '+width);
   for(const chapter of ['overview','rocket','satellite','ground','orbit']){
    const button=q.locator('#'+chapter+' [data-image]');await button.scrollIntoViewIfNeeded();
    await q.waitForFunction(id=>{const img=document.querySelector('#'+id+' img');return img.complete&&img.naturalWidth>0},chapter);
    assert(await q.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Image overflow '+chapter);
   }
   await q.locator('#rocket [data-image]').click();await q.locator('#atlasLightbox').waitFor();
   await q.locator('#imageZoom').click();assert(await q.locator('#imageZoom').getAttribute('aria-pressed')==='true','Zoom missing');
   const close=await q.locator('#imageClose').boundingBox();assert(close.y>=0&&close.y+close.height<=height,'Close unreachable');
   await q.keyboard.press('Escape');assert(!await q.locator('#atlasLightbox').isVisible(),'Escape failed');
   assert(await q.locator('#rocket [data-image]').evaluate(el=>document.activeElement===el),'Focus not returned');
   await q.locator('#stockSearch').fill('600118');assert(await q.locator('.atlas-stock:visible').count()===1,'Code filter failed');
   await q.locator('#stockLayer').selectOption('基础供给');assert(await q.locator('#stockEmpty').isVisible(),'Empty state missing');
   await q.locator('#resetStocks').click();assert(await q.locator('.atlas-stock:visible').count()===3,'Reset failed');
   await q.evaluate(()=>scrollTo(0,0));await q.waitForTimeout(250);
   if([1440,390].includes(width))await q.screenshot({path:'output/ui-qa/atlas-'+width+'.png'});
  }
  const link=await q.locator('.atlas-stock .stock-name').first().getAttribute('href');assert(link.includes('code=sh600118'),'Symbol context lost');
  await q.locator('.atlas-stock .stock-name').first().click();await q.waitForURL('**/symbol.html?code=sh600118');
  const staticResponse=await q.request.get('http://127.0.0.1:8000/sector_atlas.html');assert(staticResponse.ok(),'Static entry unavailable');
  const imageResponse=await q.request.get('http://127.0.0.1:8000/assets/sector-atlas/commercial-space/01-overview.jpg');assert(imageResponse.ok(),'Static image mapping unavailable');
  assert(!errors.length&&!failed.length,JSON.stringify({errors,failed}));
  return {widths:[1440,1024,390,360],fiveImagesLoaded:true,zoomEscapeFocus:true,stockFilters:true,symbolLink:true,staticEntry:true,errors,failed};
 }finally{await q.close()}
}
