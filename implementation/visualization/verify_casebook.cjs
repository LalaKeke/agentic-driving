// Offline browser checks; verifies saved tool I/O, navigation and downloads.
const { chromium }=require('../../runs/tools/visualization/node_modules/playwright');
const path=require('node:path'),fs=require('node:fs'),{pathToFileURL}=require('node:url');
(async()=>{
 const root=path.resolve(__dirname,'../..'),out=path.join(root,'implementation/results/casebook');
 const browser=await chromium.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage','--disable-gpu']});
 const page=await browser.newPage({viewport:{width:1440,height:1100},acceptDownloads:true});
 const errors=[],external=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(/^https?:/.test(r.url()))external.push(r.url());});
 await page.goto(pathToFileURL(path.join(out,'index.html')).href);
 const result=await page.evaluate(async()=>{
  const data=JSON.parse(document.getElementById('casebook-data').textContent),assert=(v,m)=>{if(!v)throw Error(m);};let phases=0,stages=0,notCalled=0;
  assert(data.cases.length===6,'six case views');
  for(let i=0;i<data.cases.length;i++){
   document.querySelector(`[data-case="${i}"]`).click();await document.getElementById('figure').decode();
   for(let j=0;j<data.cases[i].phases.length;j++){
    document.querySelector(`[data-phase="${j}"]`).click();phases++;
    for(let k=0;k<data.cases[i].phases[j].stages.length;k++){
     document.querySelector(`[data-tool="${k}"]`).click();const st=data.cases[i].phases[j].stages[k];
     assert(JSON.stringify(JSON.parse(document.getElementById('input-json').textContent))===JSON.stringify(st.input),'input unchanged');
     assert(JSON.stringify(JSON.parse(document.getElementById('output-json').textContent))===JSON.stringify(st.output),'output unchanged');
     if(st.status==='not_called'){assert(st.input===null&&st.output===null,'not called has no fabricated I/O');notCalled++;}
     stages++;
    }
   }
  }
  document.getElementById('expand-json').click();assert([...document.querySelectorAll('.io details')].every(x=>x.open),'expand JSON');
  for(const img of document.images)await img.decode();assert([...document.images].every(x=>x.naturalWidth>0&&x.src.startsWith('data:')),'all figure images embedded');
  return {case_views:data.cases.length,phases_checked:phases,tool_stages_checked:stages,not_called_stages_checked:notCalled};
 });
 const work=path.join(root,'runs/visualization/casebook-browser');fs.mkdirSync(work,{recursive:true});
 for(const [i,name] of [[0,'real-pedestrian'],[1,'real-construction'],[4,'synthetic-stop']]){
  await page.locator(`[data-case="${i}"]`).click();await page.locator('[data-tool="4"]').click();await page.screenshot({path:path.join(work,name+'.png'),fullPage:true});
 }
 const download=page.waitForEvent('download');await page.locator('#download-stage').click();const dl=await download;await dl.saveAs(path.join(work,'downloaded-stage.json'));const saved=JSON.parse(fs.readFileSync(path.join(work,'downloaded-stage.json')));if(saved.name!=='T4'||saved.output.verdict!=='FAIL')throw Error('JSON download content mismatch');
 await page.setViewportSize({width:390,height:844});
 for(let i=0;i<6;i++){
  await page.locator(`[data-case="${i}"]`).click();const size=await page.evaluate(()=>({page:document.documentElement.scrollWidth,window:innerWidth}));if(size.page>size.window+2)throw Error('mobile overflow '+i+': '+JSON.stringify(size));
 }
 await page.screenshot({path:path.join(work,'mobile.png'),fullPage:true});
 if(errors.length||external.length)throw Error(JSON.stringify({errors,external}));
 const report={status:'PASS',...result,downloaded_json_checked:true,mobile_width:390,javascript_errors:errors,external_network_requests:external,browser:browser.version(),playwright:require('../../runs/tools/visualization/node_modules/playwright/package.json').version};
 fs.writeFileSync(path.join(out,'browser_checks.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));await browser.close();
})().catch(e=>{console.error(e);process.exit(1);});
