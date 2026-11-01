// Actual browser checks for an already generated offline report. No model calls.
const { chromium } = require('../../runs/tools/visualization/node_modules/playwright');
const path = require('node:path');
const fs = require('node:fs');
const { pathToFileURL } = require('node:url');
(async()=>{
 const root=path.resolve(__dirname,'../..');
 const report=path.join(root,'implementation/results/visual_report/index.html');
 const browser=await chromium.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage','--disable-gpu']});
 const page=await browser.newPage({viewport:{width:1440,height:1100}});
 const errors=[],external=[];
 page.on('pageerror',e=>errors.push(e.message));
 page.on('request',r=>{if(/^https?:/.test(r.url()))external.push(r.url());});
 await page.goto(pathToFileURL(report).href);
 const result=await page.evaluate(async()=>{
  const data=JSON.parse(document.getElementById('report-data').textContent);
  const assert=(value,message)=>{if(!value)throw Error(message);};
  assert(data.cases.length===4,'four real windows');
  let frames=0,responses=0;
  for(let i=0;i<data.cases.length;i++){
   document.querySelector(`[data-case="${i}"]`).click();
   for(let j=0;j<10;j++){
    const input=document.getElementById('frame');input.value=j;input.dispatchEvent(new Event('input'));
    const image=document.getElementById('scene-image');await image.decode();
    assert(image.naturalWidth>0,'real frame decode');frames++;
   }
   for(let j=0;j<data.cases[i].responses.length;j++){
    const select=document.getElementById('response');select.value=j;select.dispatchEvent(new Event('change'));
    const actual=JSON.parse(document.getElementById('raw-output').textContent);
    assert(JSON.stringify(actual)===JSON.stringify(data.cases[i].responses[j].output),'unchanged displayed model output');responses++;
    assert(document.querySelectorAll('#availability tbody tr').length===actual.hypotheses.objects.length,'object rows');
   }
  }
  document.querySelector('[data-tab="synthetic"]').click();
  for(const key of ['accepted','rejected']){
   const choice=document.getElementById('candidate');choice.value=key;choice.dispatchEvent(new Event('change'));
   for(const t of [0,12,25]){
    const input=document.getElementById('sim-time');input.value=t;input.dispatchEvent(new Event('input'));
    assert(document.getElementById('sim-label').textContent===data.synthetic[key].points[t].time_s.toFixed(1)+'s','simulation timeline');
    assert(!document.getElementById('bev').innerHTML.includes('NaN'),'valid plot coordinates');
   }
  }
  document.getElementById('play-sim').click();
  await new Promise(r=>setTimeout(r,520));
  assert(Number(document.getElementById('sim-time').value)>0,'simulation playback advances');
  document.getElementById('play-sim').click();
  document.querySelector('[data-tab="real"]').click();document.querySelector('[data-case="0"]').click();
  document.getElementById('play-images').click();await new Promise(r=>setTimeout(r,740));
  assert(Number(document.getElementById('frame').value)===0,'causal image playback starts from oldest front frame');
  document.getElementById('play-images').click();
  for(const name of ['g2','evidence','real'])document.querySelector(`[data-tab="${name}"]`).click();
  for(const image of document.images){if(image.src)await image.decode();}
  assert([...document.images].every(i=>i.naturalWidth>0),'all images decode');
  assert([...document.querySelectorAll('img')].every(i=>i.getAttribute('src').startsWith('data:')),'standalone image payload');
  return {real_cases:4,decoded_capture_frames:frames,model_responses_checked:responses,synthetic_candidates_checked:2,software_tests_inherited:data.summary.tests.total};
 });
 const screenshots=path.join(root,'runs/visualization/browser');fs.mkdirSync(screenshots,{recursive:true});
 await page.locator('#frame').evaluate(el=>{el.value='9';el.dispatchEvent(new Event('input'));});
 await page.screenshot({path:path.join(screenshots,'desktop-real.png'),fullPage:true});
 await page.locator('[data-tab="synthetic"]').click();await page.screenshot({path:path.join(screenshots,'desktop-synthetic.png'),fullPage:true});
 await page.setViewportSize({width:390,height:844});
 for(const tab of ['real','g2','synthetic','evidence']){
  await page.locator(`[data-tab="${tab}"]`).click();
  const width=await page.evaluate(()=>({scroll:document.documentElement.scrollWidth,window:innerWidth}));
  if(width.scroll>width.window+2)throw Error('mobile overflow '+tab+': '+JSON.stringify(width));
 }
 await page.locator('[data-tab="real"]').click();await page.screenshot({path:path.join(screenshots,'mobile-real.png'),fullPage:true});
 if(errors.length||external.length)throw Error(JSON.stringify({errors,external}));
 const out={status:'PASS',...result,javascript_errors:errors,external_network_requests:external,mobile_width:390,browser:browser.version(),playwright:require('../../runs/tools/visualization/node_modules/playwright/package.json').version,screenshots_scope:'Local browser checks; screenshots in ignored runs/visualization/browser'};
 fs.writeFileSync(path.join(root,'implementation/results/visual_report/browser_checks.json'),JSON.stringify(out,null,2)+'\n');
 console.log(JSON.stringify(out));await browser.close();
})().catch(e=>{console.error(e);process.exit(1);});
