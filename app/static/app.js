const page = location.pathname.replace('/','') || 'home';
document.querySelectorAll('nav a').forEach(a=>{if(a.getAttribute('href')==='/'+page||(page==='home'&&a.getAttribute('href')==='/'))a.classList.add('active')});
const main = document.getElementById('main');
const skel = n => main.innerHTML = Array(n).fill('<div class=skeleton></div>').join('');

if(page==='home'){
  main.innerHTML = `<div class=card><h2>welcome</h2><p> Royce AI is live. Generate ad packages in <a href=/scripts style=color:#e8b04b>scripts</a>, track clients in <a href=/crm style=color:#e8b04b>crm</a>, read spend in <a href=/reporting style=color:#e8b04b>reporting</a>.</p></div>
  <div class=card><h2>engine status</h2><div id=status class=empty>loading…</div></div>`;
  fetch('/api/settings').then(r=>r.json()).then(s=>{
    document.getElementById('status').innerHTML =
      `<p><span class=dot></span>gemini ${s.gemini.configured?'ready':'no key'}</p>
       <p><span class=dot></span>openrouter ${s.openrouter.configured?'ready':'no key'}</p>
       <p style=margin-top:8px;color:#9a8d7a>failover: automatic</p>`;
  });
}
else if(page==='scripts'){
  skel(3);
  fetch('/api/settings').then(r=>r.json()).then(s=>{
    main.innerHTML = `<div class=card><h2>generate ad package</h2>
    <input id=product placeholder="product (e.g. NOISE earbuds)">
    <input id=audience placeholder="audience (e.g. young Lagos commuters)">
    <select id=platform><option>tiktok</option><option>instagram</option><option>facebook</option><option>google</option></select>
    <select id=provider><option value="">auto (gemini, failover)</option><option value=gemini>gemini</option><option value=openrouter>openrouter</option></select>
    <button onclick=gen()>✨ generate</button></div><div id=out></div>`;
  });
  window.gen = async () => {
    const out = document.getElementById('out');
    out.innerHTML = Array(6).fill('<div class=skeleton></div>').join('');
    try{
      const r = await fetch('/api/script',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({product:product.value,audience:audience.value,platform:platform.value,provider:provider.value||undefined})});
      const p = await r.json();
      if(p.error){out.innerHTML=`<div class=card>${p.error}</div>`;return}
      out.innerHTML = `<div class=card><h2>hooks</h2>${p.hooks.map((h,i)=>`<div class=hook>${i+1}. ${h}</div>`).join('')}</div>
      <div class=card><h2>script</h2>${p.script.map(s=>`<div class=hook><b>${s.beat}</b><br>${s.visual}<br><i>${s.voiceover||''}</i></div>`).join('')}</div>
      <div class=card><h2>captions</h2>${p.captions.map(c=>`<div class=hook>${c}</div>`).join('')}</div>`;
    }catch(e){out.innerHTML=`<div class=card>generation failed, try again</div>`}
  };
}
else if(page==='crm'){
  skel(3); window._leads = [];
  const load = () => fetch('/api/leads').then(r=>r.json()).then(l=>{_leads=l;render()});
  const render = () => {
    main.innerHTML = `<div class=card><h2>add lead</h2>
    <input id=n placeholder="name"><input id=b placeholder="business"><input id=c placeholder="contact"><input id=f type=date>
    <button onclick=add()>➕ add lead</button></div><div class=card><h2>leads (${_leads.length})</h2>
    ${_leads.length? `<table><tr><th>name</th><th>status</th><th>followup</th><th></th></tr>`+_leads.map(l=>`
      <tr><td>${l.name}<br><small style=color:#9a8d7a>${l.business||''} ${l.contact||''}</small></td>
      <td><span class="badge ${l.status}">${l.status}</span></td><td>${l.next_followup||'—'}</td>
      <td><select onchange=st(${l.id},this.value)><option${l.status=='open'?' selected':''}>open</option><option${l.status=='won'?' selected':''}>won</option><option${l.status=='lost'?' selected':''}>lost</option></select></td></tr>`).join('')+`</table>`
    : `<div class=empty>no leads yet 👻<br>add ur first one above</div>`}</div>`;
  };
  window.add = async () => {
    await fetch('/api/leads',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name:n.value,business:b.value,contact:c.value,next_followup:f.value||null})});
    n.value=b.value=c.value=f.value=''; load();
  };
  window.st = (id,v) => fetch('/api/leads/'+id,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:v})}).then(load);
  load();
}
else if(page==='reporting'){
  main.innerHTML = `<div class=card><h2>upload ad spend csv</h2>
  <p style=color:#9a8d7a;font-size:.85rem>columns: campaign, spend, impressions, clicks</p>
  <input type=file id=csv accept=.csv><button onclick=up()>📊 analyze</button></div><div id=out></div>`;
  window.up = async () => {
    const fd = new FormData(); fd.append('file', csv.files[0]);
    out.innerHTML = Array(4).fill('<div class=skeleton></div>').join('');
    const rows = await (await fetch('/api/report/upload',{method:'POST',body:fd})).json();
    out.innerHTML = `<div class=card><table><tr><th>campaign</th><th>spend</th><th>impr</th><th>clicks</th><th>ctr</th><th>cpc</th></tr>
    ${rows.map(r=>`<tr><td>${r.campaign}</td><td>${r.spend}</td><td>${r.impressions}</td><td>${r.clicks}</td><td>${r.ctr}%</td><td>${r.cpc}</td></tr>`).join('')}</table></div>`;
  };
}
