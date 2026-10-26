const page = location.pathname.replace('/','') || 'home';
const main = document.getElementById('main');
const esc = s => String(s??'').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const skel = n => main.innerHTML = Array(n).fill('<div class=skeleton></div>').join('');
const WEEK = 7*864e5;
// created_at is stored in UTC as "YYYY-MM-DD HH:MM:SS"
const parseUTC = s => { const t = Date.parse(String(s||'').replace(' ','T')+'Z'); return Number.isFinite(t) ? new Date(t) : null; };
const isNew = l => { const d = parseUTC(l.created_at); return !!d && Date.now()-d.getTime() < WEEK; };
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
const plural = (n, w) => `${n} ${w}${n===1?'':'s'}`;

// header + tab bar state
const TITLES = {home:'Royce AI', scripts:'generated work', crm:'leads', reporting:'reports', connectors:'connectors'};
document.getElementById('ttl').textContent = TITLES[page] || 'Royce AI';
const backBtn = document.getElementById('back');
const closeBtn = document.getElementById('close');
if(page==='home'){ backBtn.classList.add('hide'); closeBtn.classList.add('hide'); }
backBtn.onclick = () => { if(history.length>1) history.back(); else location.href='/'; };
document.querySelectorAll('.tabs a').forEach(a=>{ if(a.dataset.p===page) a.classList.add('on'); });

if(page==='home'){
  main.innerHTML = `
  <section class="card profile">
    <div class=avatar>R</div>
    <div><div class=pname>Royce AI</div><div class=psub>marketing agency agent</div></div>
  </section>
  <div class=sec>workspace</div>
  <section class=card>
    <a class=row href=/scripts><span class=ico-sq>✨</span><div class=row-main><div class=row-label>generated work</div><div class=row-sub id=cnt-work>loading…</div></div><span class=chev>›</span></a>
    <a class=row href=/crm><span class=ico-sq>🤝</span><div class=row-main><div class=row-label>leads</div><div class=row-sub id=cnt-leads>loading…</div></div><span class=chev>›</span></a>
    <a class=row href=/reporting><span class=ico-sq>📊</span><div class=row-main><div class=row-label>reports</div><div class=row-sub>campaign numbers, last 7 days</div></div><span class=chev>›</span></a>
  </section>
  <div class=sec>integrations</div>
  <section class=card>
    <a class=row href=/connectors><span class=ico-sq>🔌</span><div class=row-main><div class=row-label>connectors</div><div class=row-sub>ad platforms royce pulls numbers from</div></div><span class=status id=conn-count>…</span><span class=chev>›</span></a>
  </section>
  <p class=chat-hint><span class="pill dark sm">chat with royce →</span></p>`;

  fetch('/api/work').then(r=>r.json()).then(items=>{
    document.getElementById('cnt-work').textContent = items.length ? plural(items.length,'ad package') : 'none yet, ask royce in the chat';
  }).catch(()=>{ document.getElementById('cnt-work').textContent = 'could not load'; });
  fetch('/api/leads').then(r=>r.json()).then(leads=>{
    const open = leads.filter(l=>l.status==='open').length;
    document.getElementById('cnt-leads').textContent = leads.length ? `${plural(leads.length,'lead')} · ${open} open` : 'none yet, royce adds them in the chat';
  }).catch(()=>{ document.getElementById('cnt-leads').textContent = 'could not load'; });
  fetch('/api/collectors').then(r=>r.json()).then(st=>{
    const n = Object.values(st).filter(c=>c.configured).length;
    const el = document.getElementById('conn-count');
    el.className = 'status' + (n ? ' on' : '');
    el.innerHTML = n ? `<span class=dot></span>${n} connected` : 'none connected';
  }).catch(()=>{ document.getElementById('conn-count').textContent = 'unavailable'; });
}
else if(page==='scripts'){
  skel(2);
  fetch('/api/work').then(r=>r.json()).then(items=>{
    if(!items.length){
      main.innerHTML = `<section class=card><div class=empty>no generated work yet. ask royce in the chat to make your first ad.</div></section>`;
      return;
    }
    main.innerHTML = `<p class=count><span>${plural(items.length,'ad package')}</span><span>newest first</span></p>` + items.map(w=>`
      <section class=card>
        <div class="work-head">
          <div class=card-title>${esc(w.product)}</div>
          <div class=work-meta>${esc(w.audience)} · ${esc(w.platform)} · ${esc(ago(w.created_at))} · via ${esc(w.source||'mini app')}</div>
        </div>
        <div class=work-body>
          <div class=work-sec>hooks</div>
          ${(w.hooks||[]).map((h,i)=>`<div class=hook>${i+1}. ${esc(h)}</div>`).join('') || '<div class=sub>none</div>'}
          <div class=work-sec>script</div>
          ${(w.script||[]).map(x=>`<div class=beat><b>${esc(x.beat)}</b>${esc(x.visual)}${x.voiceover?`<i>${esc(x.voiceover)}</i>`:''}</div>`).join('') || '<div class=sub>none</div>'}
          <div class=work-sec>captions</div>
          ${(w.captions||[]).map(c=>`<div class=hook>${esc(c)}</div>`).join('') || '<div class=sub>none</div>'}
        </div>
      </section>`).join('');
  }).catch(()=>{ main.innerHTML = '<section class=card><div class=empty>could not load generated work, refresh to try again</div></section>'; });
}
else if(page==='crm'){
  skel(2);
  const load = () => fetch('/api/leads').then(r=>r.json()).then(leads=>{
    main.innerHTML = `<p class=count><span>${plural(leads.length,'lead')}</span><span>added by royce in the chat</span></p>` +
    (leads.length ? `<section class=card>` + leads.map(l=>`
      <div class=row style="cursor:default;align-items:flex-start">
        <span class=ico-sq>${esc(initials(l.name))}</span>
        <div class=row-main>
          <div class=row-label>${esc(l.name)}${isNew(l)?'<span class=newtag>new</span>':''}</div>
          <div class=row-sub>${esc(l.business||'')}${l.business && l.contact ? ' · ' : ''}${esc(l.contact||'')}</div>
          <div class=row-sub>follow-up: ${esc(l.next_followup||'none set')}</div>
          <div class=actions style="margin-top:8px">
            <span class="badge ${esc(l.status)}">${esc(l.status)}</span>
            <select class=mini onchange="st(${Number(l.id)},this.value)" aria-label="status">
              <option${l.status=='open'?' selected':''}>open</option><option${l.status=='won'?' selected':''}>won</option><option${l.status=='lost'?' selected':''}>lost</option>
            </select>
          </div>
        </div>
      </div>`).join('') + `</section>`
    : `<section class=card><div class=empty>no leads yet. tell royce in the chat to add one.</div></section>`);
  });
  window.st = (id,v) => fetch('/api/leads/'+id,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:v})}).then(load);
  load();
}
else if(page==='reporting'){
  main.innerHTML = `
    <section class=card>
      <div class=pad style="display:flex;justify-content:space-between;align-items:center;gap:12px">
        <div><div class=card-title>last 7 days</div><div class=sub id=rep-meta>merged from every connected ad platform</div></div>
        <button class="pill gray sm" id=refresh>refresh</button>
      </div>
    </section>
    <div id=rep-body>${Array(2).fill('<div class=skeleton></div>').join('')}</div>`;
  const load = async () => {
    const body = document.getElementById('rep-body');
    body.innerHTML = Array(2).fill('<div class=skeleton></div>').join('');
    try{
      const r = await fetch('/api/report/all',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({preset:'last_7d'})});
      const j = await r.json();
      if(!r.ok){ body.innerHTML = `<section class=card><div class=empty>${esc(j.error||'no report yet')}. connect an ad platform on the connectors page.</div></section>`; return; }
      document.getElementById('rep-meta').textContent = `from ${(j.configured||[]).join(', ') || 'connected platforms'}`;
      const errs = (j.errors||[]).map(e=>`<p class=sub style="padding:0 6px 10px">${esc(e.collector)}: ${esc(e.error)}</p>`).join('');
      const summary = j.summary ? `<section class=card><div class=summary>${esc(j.summary)}</div></section>` : '';
      const rows = (j.rows||[]).map(r=>`
        <section class=card>
          <div class=pad><div class=card-title>${esc(r.campaign)}</div><div class=sub>${esc(r.platform)}</div></div>
          <div class=kv><span>spend</span><b>${esc(r.spend)}</b></div>
          <div class=kv><span>impressions</span><b>${esc(r.impressions)}</b></div>
          <div class=kv><span>clicks</span><b>${esc(r.clicks)}</b></div>
          <div class=kv><span>ctr</span><b>${esc(r.ctr)}%</b></div>
          <div class=kv><span>cpc</span><b>${esc(r.cpc)}</b></div>
        </section>`).join('');
      body.innerHTML = summary + (rows || '<section class=card><div class=empty>no campaign rows for this period</div></section>') + errs;
    }catch(e){ body.innerHTML = '<section class=card><div class=empty>could not load the report, hit refresh to retry</div></section>'; }
  };
  document.getElementById('refresh').onclick = load;
  load();
}
else if(page==='connectors'){
  main.innerHTML = `<section class=card><div class=intro>connect the ad platforms royce pulls numbers from. tokens are stored on the server and never shown back.</div></section><section class=card id=conns>${Array(2).fill('<div class=skeleton style="margin:12px 16px"></div>').join('')}</section>`;
  const help = {
    meta: 'system user token, ads_read permission',
    tiktok: 'access token, reporting read scope',
    gmail: 'app password, read-only inbox',
    facebook: 'page token. insights coming soon',
    x: 'bearer token. insights coming soon',
    instagram: 'graph api token. insights coming soon',
    linkedin: 'api token. insights coming soon',
  };
  const full = {
    meta: 'System user token from Meta Business Settings > Users > System users > Generate token (ads_read permission). account id is the ad account number without act_.',
    tiktok: 'Access token from your TikTok for Business developer app (Reporting read scope), plus your TikTok Ads Manager advertiser id.',
    gmail: 'Google Account > Security > 2-Step Verification > App passwords. Create one called Royce AI and paste the 16 characters as the app password, with your gmail address. Royce reads your inbox only.',
    facebook: 'Facebook Page or Meta Graph token. Saved now so it shows as connected. Live insights are coming soon, so no data is pulled yet.',
    x: 'X API bearer token from the developer portal. Saved now. Live insights are coming soon.',
    instagram: 'Instagram Graph API token for your business account. Saved now. Live insights are coming soon.',
    linkedin: 'LinkedIn API token. Saved now. Live insights are coming soon.',
  };
  const icons = {meta:'Ⓜ', tiktok:'♪', google:'G', gmail:'✉', facebook:'f', x:'𝕏', instagram:'◎', linkedin:'in'};
  const draw = async () => {
    const st = await (await fetch('/api/collectors')).json();
    document.getElementById('conns').innerHTML = Object.entries(st).map(([name,c])=>{
      const isGoogle = name==='google';
      const sub = isGoogle ? 'needs oauth' : (c.configured ? (c.account_id ? `account ${c.account_id}` : 'token saved') : (help[name]||c.token_help||''));
      const right = isGoogle
        ? '<span class="status soon">coming soon</span>'
        : (c.configured ? '<span class="status on"><span class=dot></span>connected</span>' : '<button class=connect type=button>connect</button>');
      const head = `<button class=row type=button ${isGoogle?'disabled style="cursor:default"':`onclick="toggleC('${name}')"`}>
          <span class=ico-sq>${esc(icons[name]||'•')}</span>
          <div class=row-main><div class=row-label>${esc(c.label)}</div><div class=row-sub>${esc(sub)}</div></div>
          ${right}${isGoogle?'':'<span class=chev>›</span>'}
        </button>`;
      const body = isGoogle ? '' : `
        <div class=fields id=fx-${name} hidden>
          <div class=sub style="margin:2px 0 8px">${esc(full[name]||c.token_help||'')}</div>
          <input id=tok-${name} placeholder="${esc(c.token_label||'access token')}" type=password autocomplete=off>
          <input id=acc-${name} placeholder="${esc(c.account_label)}" autocomplete=off>
          <div class=actions>
            <button class="pill dark sm" type=button onclick="saveC('${name}')">save</button>
            <button class=txt type=button onclick="testC('${name}')">test</button>
          </div>
          <div id=msg-${name} class=msg></div>
        </div>`;
      return `<div>${head}${body}</div>`;
    }).join('');
  };
  window.toggleC = (name) => { const el = document.getElementById('fx-'+name); if(el) el.hidden = !el.hidden; };
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
