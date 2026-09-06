// Playwright MCP regression, isolated server on 8877; sample indices are local fixtures.
async (page) => {
 const q=await page.context().newPage(), errors=[];
 const assert=(ok,message)=>{if(!ok)throw new Error(message)};
 q.on('pageerror',e=>errors.push(e.message));
 await q.route('**/api/trainer/market-context?*',route=>route.fulfill({json:{market_date:'2026-09-01',a_share:['上证指数','深证成指','创业板指','沪深300','科创50'].map((name,i)=>({name,code:String(i),price:3000+i,change_pct:i-2,source:'minute',time:'09:31'})),overseas:['道琼斯','纳斯达克','标普500','恒生指数','韩国综合'].map(name=>({name,price:23000,change_pct:-0.25,source:'daily_close',bar_date:'2026-08-31'}))}}));
 try{
  await q.setViewportSize({width:1440,height:900});
  await q.goto('http://127.0.0.1:8877/trading_trainer.html?code=sh600000');
  await q.waitForFunction(()=>document.querySelector('#code').value.includes('sh600000'));
  if(await q.locator('#game').isHidden())await q.locator('#start').click();
  await q.waitForFunction(()=>typeof state!=='undefined'&&state?.minute_points.length>0);
  await q.locator('.training-index-items>div').first().waitFor();
  assert(await q.locator('#pickChartPrice').count()===0,'pickChartPrice button still present');
  assert(await q.locator('#chartPricePicker').count()===0,'chartPricePicker banner still present');
  const before=await q.evaluate(()=>JSON.stringify({account:state.account,time:state.time,orders:state.orders}));
  // Desktop live hover (side dock): fill from price axis on both charts.
  for(const side of ['Buy','Sell']){
   await q.locator('#open'+side).click();await q.locator('#manualOrderType').selectOption('limit');
   await q.locator('#manualLimitPrice').fill('1');
   const r=await q.locator('#dailyChart').boundingBox(),x=r.x+r.width/2,y=r.y+150;
   const expected=await q.evaluate(()=>dailyChart.convertFromPixel({xAxisIndex:0,yAxisIndex:0},[dailyChart.getWidth()/2,150])[1].toFixed(2));
   await q.mouse.move(x,y);
   assert(await q.locator('#manualLimitPrice').inputValue()===expected,'Hover price differs from price axis');
   await q.keyboard.press('Escape');
  }
  // Minute chart early-session: any price-pane Y fills (no revealed-slot gate).
  await q.locator('#openBuy').click();await q.locator('#manualOrderType').selectOption('limit');
  await q.locator('#manualLimitPrice').fill('1');
  const minuteFill=await q.evaluate(()=>{
    const w=minuteChart.getWidth(),h=minuteChart.getHeight();
    const y=Math.round(h*0.25);
    const expected=minuteChart.convertFromPixel({xAxisIndex:0,yAxisIndex:0},[w*0.75,y])[1];
    minuteChart.getZr().trigger('mousemove',{offsetX:w*0.75,offsetY:y});
    return{expected:expected.toFixed(2),value:document.getElementById('manualLimitPrice').value};
  });
  assert(minuteFill.value===minuteFill.expected,'Minute early-session hover did not fill from price axis '+JSON.stringify(minuteFill));
  // Volume pane must not change price.
  const current=await q.locator('#manualLimitPrice').inputValue();
  await q.evaluate(()=>minuteChart.getZr().trigger('mousemove',{offsetX:minuteChart.getWidth()/2,offsetY:minuteChart.getHeight()*0.85}));
  assert(await q.locator('#manualLimitPrice').inputValue()===current,'Volume pane used for price');
  await q.keyboard.press('Escape');
  for(const [width,height] of [[1440,900],[1024,768],[390,844],[360,800]]){
   await q.setViewportSize({width,height});await q.evaluate(()=>scrollTo(0,document.body.scrollHeight));
   const result=await q.locator('#trainingIndices').evaluate(el=>({top:el.getBoundingClientRect().top,bottom:el.getBoundingClientRect().bottom,width:document.documentElement.scrollWidth,items:[...el.querySelectorAll('.training-index-items>div')].map(x=>x.getBoundingClientRect().right)}));
   assert(result.top===56&&result.bottom<height,'Indices scrolled away');
   assert(result.width<=width,'Page overflow');
   assert(await q.locator('.training-index-row').count()===2,'Missing domestic or overseas row');
  }
  // Mobile silent pick: focus limit input closes sheet; tap chart confirms; Esc cancels.
  await q.setViewportSize({width:390,height:844});await q.getByRole('tab',{name:'分时',exact:true}).click();
  await q.locator('#openBuy').click();await q.locator('#manualOrderType').selectOption('limit');
  const prior=await q.locator('#manualLimitPrice').inputValue();
  await q.locator('#manualLimitPrice').focus();await q.waitForTimeout(100);
  assert(await q.locator('#tradeModal').isHidden(),'Mobile focus did not enter silent pick');
  assert(await q.locator('#chartPricePicker').count()===0,'Banner appeared in silent pick');
  const tap=await q.evaluate(()=>{const r=minuteChart.getDom().getBoundingClientRect(),p=minuteChart.convertToPixel({xAxisIndex:0,yAxisIndex:0},[0,state.market.price]);return{x:r.x+p[0],y:r.y+p[1]}});
  await q.mouse.move(tap.x,tap.y);
  const filled=await q.evaluate(()=>document.getElementById('manualLimitPrice').value);
  assert(filled!==prior&&+filled>0,'Mobile hover did not fill price');
  await q.mouse.click(tap.x,tap.y);
  if(!await q.locator('#tradeModal').isVisible()){await q.screenshot({path:'output/ui-qa/picker-debug.png'});throw new Error('Mobile chart tap did not return to order '+JSON.stringify(tap));}
  // Esc cancel restores previous
  await q.locator('#manualLimitPrice').evaluate(el=>{el.blur()});
  await q.locator('#manualLimitPrice').focus();await q.waitForTimeout(100);
  const beforeCancel=await q.evaluate(()=>document.getElementById('manualLimitPrice').value);
  await q.evaluate(()=>{const w=minuteChart.getWidth();minuteChart.getZr().trigger('mousemove',{offsetX:w*0.4,offsetY:80});});
  await q.keyboard.press('Escape');
  assert(await q.locator('#tradeModal').isVisible(),'Esc cancel did not restore modal');
  assert(await q.locator('#manualLimitPrice').inputValue()===beforeCancel,'Cancel did not restore price');
  await q.keyboard.press('Escape');
  assert(await q.evaluate(()=>JSON.stringify({account:state.account,time:state.time,orders:state.orders}))===before,'Picking changed simulation');
  await q.locator('#minuteChart').scrollIntoViewIfNeeded();
  await q.screenshot({path:'output/ui-qa/trainer-fixed-indices-mobile.png'});
  await q.setViewportSize({width:1440,height:900});await q.locator('#minuteChart').scrollIntoViewIfNeeded();
  await q.screenshot({path:'output/ui-qa/trainer-fixed-indices-desktop.png'});
  assert((await q.locator('#minuteChart').boundingBox()).height===480,'Minute chart not enlarged');
  assert((await q.locator('.metrics').boundingBox()).height<100,'Account summary not compact');
  assert(!errors.length,JSON.stringify(errors));
  return {buySellHover:true,cancelRestores:true,fixedIndicesAtFourSizes:true,minuteAxisFill:true,volumeExcluded:true,noPickChrome:true,simulationUnchanged:true,errors};
 }finally{await q.close()}
}
