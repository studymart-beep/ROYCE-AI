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
        <a class=qa href=/scripts><span class=ico>✦</span>View generated ads<span class=chev>›</span></a>
        <a class=qa href=/crm><span class=ico>◎</span>View leads<span class=chev>›</span></a>
        <a class=qa href=/reporting><span class=ico>▤</span>View reports<span class=chev>›</span></a>
        <p class=muted style="margin-top:10px">tell Royce in the chat what u need. he does the work.</p>
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
  skel(3);
  const agoOrDate = s => ago(s) || '';
  fetch('/api/work').then(r=>r.json()).then(items=>{
    if(!items.length){
      main.innerHTML = `<div class="card empty"><h2 class=h2 style="margin-bottom:6px">Generated work</h2>ask Royce in the chat to generate ur first ad</div>`;
      return;
    }
    main.innerHTML = `<div class=hero style="margin-bottom:4px"><div><p class=eyebrow>✦ Generated work</p><h1 class=h1>${items.length} ad package${items.length>1?'s':''}</h1><p class=sub>newest first. tell Royce in the chat to make more.</p></div></div>`
      + items.map(w=>`
      <div class=card>
        <div class=card-head><div><h2 class=h2>${esc(w.product)}</h2><p class=muted>${esc(w.audience)} · ${esc(w.platform)} · ${esc(agoOrDate(w.created_at))} · via ${esc(w.source||'mini app')}</p></div></div>
        <p class=stat-label style="margin-top:6px">hooks</p>
        ${(w.hooks||[]).map((h,i)=>`<div class=hook>${i+1}. ${esc(h)}</div>`).join('')}
        <p class=stat-label style="margin-top:12px">script</p>
        ${(w.script||[]).map(x=>`<div class=hook><b>${esc(x.beat)}</b><br>${esc(x.visual)}<br><i>${esc(x.voiceover||'')}</i></div>`).join('')}
        <p class=stat-label style="margin-top:12px">captions</p>
        ${(w.captions||[]).map(c=>`<div class=hook>${esc(c)}</div>`).join('')}
      </div>`).join('');
  }).catch(()=>{ main.innerHTML = '<div class="card empty">could not load generated work, refresh to try again</div>'; });
}
else if(page==='crm'){
  skel(3);
  const load = () => fetch('/api/leads').then(r=>r.json()).then(leads=>{
    main.innerHTML = `<div class=card><div class=card-head><h2 class=h2>Leads (${leads.length})</h2><span class=muted>leads are added by Royce in the chat</span></div>
    ${leads.length? `<div class=table-wrap><table><tr><th>name</th><th>status</th><th>follow-up</th><th>update</th></tr>`+leads.map(l=>`
      <tr><td><b>${esc(l.name)}</b>${isNew(l)?'<span class=pill-new>New</span>':''}<br><span class=muted>${esc(l.business||'')} ${esc(l.contact||'')}</span></td>
      <td><span class="badge ${esc(l.status)}">${esc(l.status)}</span></td><td>${esc(l.next_followup||'-')}</td>
      <td><select onchange="st(${Number(l.id)},this.value)"><option${l.status=='open'?' selected':''}>open</option><option${l.status=='won'?' selected':''}>won</option><option${l.status=='lost'?' selected':''}>lost</option></select></td></tr>`).join('')+`</table></div>`
    : `<div class=empty>no leads yet. tell Royce in the chat to add one.</div>`}</div>`;
  });
  window.st = (id,v) => fetch('/api/leads/'+id,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:v})}).then(load);
  load();
}
else if(page==='reporting'){
  main.innerHTML = `<div class=card><div class=card-head><div><h2 class=h2>Campaign report</h2><p class=muted id=rep-meta>merged from every connected ad platform</p></div>
    <button class="btn sm" id=refresh>↻ refresh</button></div>
    <div id=rep-body><div class=skeleton></div><div class=skeleton></div></div></div>`;
  const esc2 = esc;
  const table = rows => `<div class=table-wrap><table><tr><th>platform</th><th>campaign</th><th>spend</th><th>impr</th><th>clicks</th><th>ctr</th><th>cpc</th></tr>
    ${rows.map(r=>`<tr><td>${esc2(r.platform)}</td><td>${esc2(r.campaign)}</td><td>${esc2(r.spend)}</td><td>${esc2(r.impressions)}</td><td>${esc2(r.clicks)}</td><td>${esc2(r.ctr)}%</td><td>${esc2(r.cpc)}</td></tr>`).join('')}</table></div>`;
  const load = async () => {
    const body = document.getElementById('rep-body');
    body.innerHTML = '<div class=skeleton></div><div class=skeleton></div>';
    try{
      const r = await fetch('/api/report/all',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({preset:'last_7d'})});
      const j = await r.json();
      if(!r.ok){ body.innerHTML = `<p class=muted>${esc(j.error||'no report yet')}. connect an ad platform on the connectors page.</p>`; return; }
      document.getElementById('rep-meta').textContent = `last 7 days · from ${(j.configured||[]).join(', ') || 'connected platforms'}`;
      const errs = (j.errors||[]).map(e=>`<p class=muted>${esc(e.collector)}: ${esc(e.error)}</p>`).join('');
      const summary = j.summary ? `<div class=hook>${esc(j.summary)}</div>` : '';
      body.innerHTML = summary + (j.rows && j.rows.length ? table(j.rows) : '<div class=empty>no campaign rows for this period</div>') + errs;
    }catch(e){ body.innerHTML = '<p class=muted>could not load the report, hit refresh to retry</p>'; }
  };
  document.getElementById('refresh').onclick = load;
  load();
}
else if(page==='connectors'){
  main.innerHTML = `<div class=card><h2 class=h2 style="margin-bottom:6px">Connectors</h2><p class=muted>connect the ad platforms royce pulls numbers from. tokens are stored on the server and never shown back.</p></div><div id=conns><div class=skeleton></div></div>`;
  const help = {
    meta: 'System user token from Meta Business Settings > Users > System users > Generate token (ads_read permission). account id is the ad account number without act_.',
    tiktok: 'Access token from your TikTok for Business developer app (Reporting read scope), plus your TikTok Ads Manager advertiser id.',
  };
  const draw = async () => {
    const st = await (await fetch('/api/collectors')).json();
    document.getElementById('conns').innerHTML = Object.entries(st).map(([name,c])=>`
      <div class=card>
        <div class=card-head><div><h2 class=h2><span class="dot ${c.configured?'':'off'}"></span>${esc(c.label)}</h2>
        <p class=muted>${c.configured?`connected (${esc(c.account_id)})`:'not connected'}</p></div></div>
        ${name==='google' ? '<p class=muted>needs oauth, coming soon</p>' : `
        <p class=muted>${esc(help[name]||c.token_help||'')}</p>
        <input id=tok-${name} placeholder="access token" type=password>
        <input id=acc-${name} placeholder="${esc(c.account_label)}">
        <div class=btn-row><button class="btn sm" onclick="saveC('${name}')">save</button><button class="btn sm ghost" onclick="testC('${name}')">test</button></div>
        <div id=msg-${name} class=muted></div>`}
      </div>`).join('');
  };
  window.saveC = async (name) => {
    const r = await fetch('/api/collectors',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,token:document.getElementById('tok-'+name).value,account_id:document.getElementById('acc-'+name).value})});
    const j = await r.json();
    document.getElementById('msg-'+name).textContent = r.ok ? 'saved' : (j.error||'save failed');
    if(r.ok) draw();
  };
  window.testC = async (name) => {
    const m = document.getElementById('msg-'+name); m.textContent = 'testing…';
    const r = await fetch(`/api/collectors/${name}/test`,{method:'POST'});
    const j = await r.json();
    m.textContent = j.ok ? `live ✓ ${j.account_name||''}` : (j.error||'test failed');
  };
  draw();
}
