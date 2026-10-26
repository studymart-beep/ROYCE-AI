const page = location.pathname.replace('/','') || 'home';
document.querySelectorAll('.nav a').forEach(a=>{
  const h = a.getAttribute('href');
  if(h==='/'+page || (page==='home' && h==='/')) a.classList.add('active');
});
const main = document.getElementById('main');
const skel = n => main.innerHTML = Array(n).fill('<div class=skeleton></div>').join('');
const esc = s => String(s??'').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const WEEK = 7*864e5;
// created_at is stored in UTC as "YYYY-MM-DD HH:MM:SS"
const parseUTC = s => { const t = Date.parse(String(s||'').replace(' ','T')+'Z'); return Number.isFinite(t) ? new Date(t) : null; };
const isNew = l => { const d = parseUTC(l.created_at); return !!d && Date.now()-d.getTime() < WEEK; };
const dayKey = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
const ago = s => {
  const d = parseUTC(s); if(!d) return '';
  const m = Math.round((Date.now()-d.getTime())/60000);
  if(m<1) return 'just now';
  if(m<60) return `${m}m ago`;
  const h = Math.round(m/60);
  if(h<24) return `${h}h ago`;
  return `${Math.round(h/24)}d ago`;
};
const initials = n => (String(n||'?').trim().split(/\s+/).slice(0,2).map(w=>w[0]||'').join('') || '?').toUpperCase();
const greet = () => { const h = new Date().getHours(); return h<5?'Good night':h<12?'Good morning':h<17?'Good afternoon':'Good evening'; };
const fmtDate = () => new Date().toLocaleDateString('en-US',{weekday:'short',month:'short',day:'numeric',year:'numeric'});

if(page==='home'){
  main.innerHTML = `
  <section class=hero>
    <div><p class=eyebrow>✦ Dashboard</p><h1 class=h1>${greet()}, Royce</h1><p class=sub>Here's what's happening with your growth today.</p></div>
    <div class=date>${fmtDate()}</div>
  </section>
  <section class=stats id=stats>${Array(4).fill('<div class="card stat"><div class=skeleton></div><div class=skeleton></div></div>').join('')}</section>
  <div class=cols>
    <div class=col-main>
      <div class=card>
        <div class=card-head><div><h2 class=h2>Lead activity</h2><p class=muted>New leads added per day, last 7 days.</p></div><span class=muted id=week-total></span></div>
        <div class="chart" id=chart><div class=skeleton></div></div>
      </div>
      <div class=card>
        <div class=card-head><h2 class=h2>Recent leads</h2><a class=link href=/crm>View all →</a></div>
        <div id=recent><div class=skeleton></div><div class=skeleton></div></div>
      </div>
    </div>
    <div class=col-side>
      <div class=card>
        <h2 class=h2 style="margin-bottom:6px">Quick actions</h2>
        <a class=qa href=/scripts><span class=ico>✦</span>Generate an ad script<span class=chev>›</span></a>
        <a class=qa href=/crm><span class=ico>+</span>Add a lead<span class=chev>›</span></a>
        <a class=qa href=/reporting><span class=ico>▤</span>Pull an ad report<span class=chev>›</span></a>
      </div>
      <section class=cta>
        <span class=mark>↗</span>
        <h3>More Customers.<br>Less Guesswork.</h3>
        <p>Ad scripts, lead tracking and campaign reporting in one place.</p>
        <a href=/scripts>View Our Services →</a>
      </section>
      <div class=card>
        <h2 class=h2 style="margin-bottom:8px">Engine status</h2>
        <div id=status class=muted>loading…</div>
      </div>
    </div>
  </div>`;

  Promise.all([
    fetch('/api/report/summary').then(r=>r.json()),
    fetch('/api/leads').then(r=>r.json()),
    fetch('/api/settings').then(r=>r.json()),
  ]).then(([s, leads, st])=>{
    const total = s.total||0, open = (s.by_status||{}).open||0, won = (s.by_status||{}).won||0;
    const overdue = (s.overdue||[]).length;
    const newWeek = leads.filter(isNew).length;
    const winRate = total ? Math.round(won/total*100) : 0;
    const tiles = [
      {label:'Total leads', val:total, ico:'◎', cls:'', note: newWeek ? `+${newWeek} added in last 7 days` : 'none added in last 7 days', nc: newWeek?'':'neutral'},
      {label:'Open leads', val:open, ico:'◷', cls:'y', note:'active in your pipeline', nc:'neutral'},
      {label:'Won', val:won, ico:'✓', cls:'g', note:`${winRate}% of all leads`, nc:''},
      {label:'Overdue follow-ups', val:overdue, ico:'!', cls:'r', note: overdue ? 'follow up today' : 'all caught up', nc: overdue?'warn':'neutral'},
    ];
    document.getElementById('stats').innerHTML = tiles.map(t=>`
      <div class="card stat">
        <div class=stat-top><span class=stat-label>${t.label}</span><span class="ico ${t.cls}">${t.ico}</span></div>
        <div class=stat-val>${t.val}</div>
        <div class="stat-note ${t.nc}">${esc(t.note)}</div>
      </div>`).join('');

    // leads created per local day, last 7 days (real data from the CRM)
    const days = [];
    for(let i=6;i>=0;i--){ const d = new Date(); d.setHours(0,0,0,0); d.setDate(d.getDate()-i); days.push(d); }
    const counts = days.map(d => leads.filter(l=>{ const x = parseUTC(l.created_at); return x && dayKey(x)===dayKey(d); }).length);
    const max = Math.max(1, ...counts);
    const W=640, H=220, pl=36, pr=12, pt=14, pb=28;
    const X = i => pl + i*(W-pl-pr)/6;
    const Y = c => H-pb-(c/max)*(H-pt-pb);
    const pts = counts.map((c,i)=>[X(i),Y(c)]);
    const line = pts.map((p,i)=>(i?'L':'M')+p[0].toFixed(1)+' '+p[1].toFixed(1)).join(' ');
    const area = line + ` L${X(6)} ${H-pb} L${X(0)} ${H-pb} Z`;
    const grid = [0, max/2, max].map(v=>{ const y = Y(v); return `<line x1=${pl} x2=${W-pr} y1=${y} y2=${y} stroke="#EAE3D6" stroke-dasharray="3 4"/><text x=${pl-8} y=${y+4} text-anchor=end>${Math.round(v)}</text>`; }).join('');
    const xl = days.map((d,i)=>`<text x=${X(i)} y=${H-6} text-anchor=middle>${d.toLocaleDateString('en-US',{weekday:'short'})}</text>`).join('');
    const dots = pts.map(p=>`<circle cx=${p[0]} cy=${p[1]} r=4 fill="#F26B1D" stroke="#fff" stroke-width=2></circle>`).join('');
    document.getElementById('chart').innerHTML = `
      <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="new leads per day, last 7 days">
        <defs><linearGradient id=fillg x1=0 x2=0 y1=0 y2=1><stop offset="0" stop-color="#F26B1D" stop-opacity=".22"/><stop offset="1" stop-color="#F26B1D" stop-opacity="0"/></linearGradient></defs>
        ${grid}${xl}
        <path d="${area}" fill="url(#fillg)"></path>
        <path d="${line}" fill="none" stroke="#F26B1D" stroke-width="2.5" stroke-linejoin="round"></path>
        ${dots}
      </svg>
      ${newWeek ? '' : '<p class=muted style="text-align:center;margin-top:6px">no new leads this week yet</p>'}`;
    document.getElementById('week-total').textContent = `${newWeek} this week`;

    const recent = [...leads].sort((a,b)=>String(b.created_at||'').localeCompare(String(a.created_at||''))).slice(0,5);
    document.getElementById('recent').innerHTML = recent.length ? recent.map((l,i)=>`
      <div class=lead>
        <span class="av ${['','g','f'][i%3]}">${esc(initials(l.name))}</span>
        <div class=lead-main>
          <div class=lead-name>${esc(l.name)}${isNew(l)?'<span class=pill-new>New</span>':''}</div>
          <div class=lead-sub>${esc(l.business||'')}${l.business && l.contact ? ' · ' : ''}${esc(l.contact||'')}</div>
        </div>
        <div class=lead-time>${ago(l.created_at)}</div>
      </div>`).join('') : '<div class=empty>no leads yet. add your first one in CRM.</div>';
  }).catch(()=>{
    document.getElementById('stats').innerHTML = '<div class="card empty" style="grid-column:1/-1">could not load crm numbers, refresh to try again</div>';
  });

  fetch('/api/settings').then(r=>r.json()).then(s=>{
    document.getElementById('status').innerHTML =
      `<p><span class=dot></span>gemini ${s.gemini.configured?'ready':'no key'}</p>
       <p><span class=dot></span>openrouter ${s.openrouter.configured?'ready':'no key'}</p>
       <p class=muted style="margin-top:6px">failover: automatic</p>`;
  }).catch(()=>{});
}
else if(page==='scripts'){
  main.innerHTML = `<div class=card><h2 class=h2 style="margin-bottom:8px">Generate ad package</h2>
    <input id=product placeholder="product (e.g. NOISE earbuds)">
    <input id=audience placeholder="audience (e.g. young Lagos commuters)">
    <select id=platform><option>tiktok</option><option>instagram</option><option>facebook</option><option>google</option></select>
    <select id=provider><option value="">auto (gemini, failover)</option><option value=gemini>gemini</option><option value=openrouter>openrouter</option></select>
    <button onclick=gen()>✦ generate</button></div><div id=out></div>`;
  window.gen = async () => {
    const out = document.getElementById('out');
    out.innerHTML = Array(6).fill('<div class=skeleton></div>').join('');
    try{
      const r = await fetch('/api/script',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({product:product.value,audience:audience.value,platform:platform.value,provider:provider.value||undefined})});
      const p = await r.json();
      if(p.error){out.innerHTML=`<div class=card>${esc(p.error)}</div>`;return}
      out.innerHTML = `<div class=card><h2 class=h2 style="margin-bottom:8px">Hooks</h2>${p.hooks.map((h,i)=>`<div class=hook>${i+1}. ${esc(h)}</div>`).join('')}</div>
      <div class=card><h2 class=h2 style="margin-bottom:8px">Script</h2>${p.script.map(s=>`<div class=hook><b>${esc(s.beat)}</b><br>${esc(s.visual)}<br><i>${esc(s.voiceover||'')}</i></div>`).join('')}</div>
      <div class=card><h2 class=h2 style="margin-bottom:8px">Captions</h2>${p.captions.map(c=>`<div class=hook>${esc(c)}</div>`).join('')}</div>`;
    }catch(e){out.innerHTML=`<div class=card>generation failed, try again</div>`}
  };
}
else if(page==='crm'){
  skel(3); window._leads = [];
  const load = () => fetch('/api/leads').then(r=>r.json()).then(l=>{_leads=l;render()});
  const render = () => {
    main.innerHTML = `<div class=card><h2 class=h2 style="margin-bottom:8px">Add lead</h2>
    <input id=n placeholder="name"><input id=b placeholder="business"><input id=c placeholder="contact"><input id=f type=date>
    <button onclick=add()>+ add lead</button></div>
    <div class=card><div class=card-head><h2 class=h2>Leads (${_leads.length})</h2></div>
    ${_leads.length? `<div class=table-wrap><table><tr><th>name</th><th>status</th><th>follow-up</th><th></th></tr>`+_leads.map(l=>`
      <tr><td><b>${esc(l.name)}</b>${isNew(l)?'<span class=pill-new>New</span>':''}<br><span class=muted>${esc(l.business||'')} ${esc(l.contact||'')}</span></td>
      <td><span class="badge ${esc(l.status)}">${esc(l.status)}</span></td><td>${esc(l.next_followup||'-')}</td>
      <td><select onchange=st(${Number(l.id)},this.value)><option${l.status=='open'?' selected':''}>open</option><option${l.status=='won'?' selected':''}>won</option><option${l.status=='lost'?' selected':''}>lost</option></select></td></tr>`).join('')+`</table></div>`
    : `<div class=empty>no leads yet<br>add your first one above</div>`}</div>`;
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
  main.innerHTML = `<div class=card><h2 class=h2 style="margin-bottom:6px">Connected platforms</h2>
  <p class=muted>tokens are stored on the server and never shown back.</p>
  <div id=platforms><div class=skeleton></div></div></div>
  <div class=card><h2 class=h2 style="margin-bottom:8px">Pull report</h2>
  <select id=preset><option value=last_7d>last 7 days</option><option value=last_14d>last 14 days</option><option value=last_30d>last 30 days</option><option value=yesterday>yesterday</option><option value=today>today</option></select>
  <button onclick=pull()>Pull report</button></div>
  <div class=card><h2 class=h2 style="margin-bottom:6px">Upload ad spend CSV</h2>
  <p class=muted>columns: campaign, spend, impressions, clicks</p>
  <input type=file id=csv accept=.csv><button onclick=up()>Analyze</button></div><div id=out></div>`;
  const table = rows => `<div class=card><div class=table-wrap><table><tr><th>platform</th><th>campaign</th><th>spend</th><th>impr</th><th>clicks</th><th>ctr</th><th>cpc</th></tr>
    ${rows.map(r=>`<tr><td>${esc(r.platform)}</td><td>${esc(r.campaign)}</td><td>${esc(r.spend)}</td><td>${esc(r.impressions)}</td><td>${esc(r.clicks)}</td><td>${esc(r.ctr)}%</td><td>${esc(r.cpc)}</td></tr>`).join('')}</table></div></div>`;
  const loadPlatforms = async () => {
    const st = await (await fetch('/api/collectors')).json();
    document.getElementById('platforms').outerHTML = `<div id=platforms>` + Object.entries(st).map(([name,c])=>`
      <div class=conn>
        <p><span class="dot ${c.configured?'':'off'}"></span><b>${esc(c.label)}</b> ${c.configured?`<span class=muted>connected (${esc(c.account_id)})</span>`:'<span class=muted>not connected</span>'}</p>
        <p class=muted>${esc(c.token_help)}</p>
        ${name==='google' ? '' : `<div class=row-inputs><input id=tok-${name} placeholder="access token" type=password><input id=acc-${name} placeholder="${esc(c.account_label)}">
          <div class=btn-row><button class="btn sm" onclick="saveC('${name}')">save</button><button class="btn sm ghost" onclick="testC('${name}')">test</button></div>
          <div id=msg-${name} class=muted></div></div>`}
      </div>`).join('') + `</div>`;
  };
  window.saveC = async (name) => {
    const r = await fetch('/api/collectors',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,token:document.getElementById('tok-'+name).value,account_id:document.getElementById('acc-'+name).value})});
    const j = await r.json();
    document.getElementById('msg-'+name).textContent = r.ok ? 'saved' : (j.error||'save failed');
    if(r.ok) loadPlatforms();
  };
  window.testC = async (name) => {
    const m = document.getElementById('msg-'+name); m.textContent = 'testing…';
    const r = await fetch(`/api/collectors/${name}/test`,{method:'POST'});
    const j = await r.json();
    m.textContent = j.ok ? `live ✓ ${j.account_name||''}` : (j.error||'test failed');
  };
  window.pull = async () => {
    out.innerHTML = Array(4).fill('<div class=skeleton></div>').join('');
    const r = await fetch('/api/report/all',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({preset:preset.value})});
    const j = await r.json();
    if(!r.ok){ out.innerHTML = `<div class=card><p>${esc(j.error)}</p></div>`; return; }
    out.innerHTML = (j.errors||[]).map(e=>`<div class=card><p>${esc(e.collector)}: ${esc(e.error)}</p></div>`).join('') + (j.rows.length ? table(j.rows) : '<div class=card><p>no campaign rows for this period</p></div>');
  };
  window.up = async () => {
    const fd = new FormData(); fd.append('file', csv.files[0]);
    out.innerHTML = Array(4).fill('<div class=skeleton></div>').join('');
    const rows = await (await fetch('/api/report/upload',{method:'POST',body:fd})).json();
    out.innerHTML = Array.isArray(rows) ? table(rows.map(r=>({platform:'csv',...r}))) : `<div class=card><p>${esc(rows.error||'upload failed')}</p></div>`;
  };
  loadPlatforms();
}
