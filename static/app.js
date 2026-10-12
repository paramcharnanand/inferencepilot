const $=id=>document.getElementById(id);
$('go').onclick=async()=>{await fetch('/start',{method:'POST',body:JSON.stringify({total:+$('total').value,start_conc:+$('conc').value,slo_ms:+$('slo').value,adaptive:$('ad').checked})})};
function draw(ev,slo){const c=$('c'),dpr=devicePixelRatio||1,W=c.clientWidth,H=c.clientHeight;c.width=W*dpr;c.height=H*dpr;const x=c.getContext('2d');x.scale(dpr,dpr);x.clearRect(0,0,W,H);
const cs=getComputedStyle(document.documentElement),mu=cs.getPropertyValue('--mu'),ac=cs.getPropertyValue('--ac'),bl=cs.getPropertyValue('--bl'),bad=cs.getPropertyValue('--bad'),ln=cs.getPropertyValue('--line');
const L=44,R=40,T=10,B=24,n=Math.max(ev.length,2);const mx=Math.max(slo*1.2,...ev.map(e=>e.p95),1),mc=Math.max(8,...ev.map(e=>e.conc));
const X=i=>L+(W-L-R)*i/(n-1),Y=v=>T+(H-T-B)*(1-v/mx),Y2=v=>T+(H-T-B)*(1-v/mc);
x.strokeStyle=ln;x.fillStyle=mu;x.font='11px sans-serif';for(let k=0;k<=4;k++){const v=mx*k/4,y=Y(v);x.beginPath();x.moveTo(L,y);x.lineTo(W-R,y);x.stroke();x.fillText(Math.round(v),4,y+4)}
x.setLineDash([5,4]);x.strokeStyle=bad;x.beginPath();x.moveTo(L,Y(slo));x.lineTo(W-R,Y(slo));x.stroke();x.setLineDash([]);
x.fillStyle=bl;ev.forEach((e,i)=>{const bw=Math.min(28,(W-L-R)/n*0.5);x.globalAlpha=.35;x.fillRect(X(i)-bw/2,Y2(e.conc),bw,H-B-Y2(e.conc));x.globalAlpha=1;x.fillText(e.conc,X(i)-6,Y2(e.conc)-3)});
x.strokeStyle=ac;x.lineWidth=2.5;x.beginPath();ev.forEach((e,i)=>{i?x.lineTo(X(i),Y(e.p95)):x.moveTo(X(i),Y(e.p95))});x.stroke();x.fillStyle=ac;ev.forEach((e,i)=>{x.beginPath();x.arc(X(i),Y(e.p95),4,0,7);x.fill()});
x.fillStyle=mu;ev.forEach((e,i)=>x.fillText('w'+e.wave,X(i)-8,H-6));x.fillStyle=bad;x.fillText('target',W-R+2,Y(slo)+4)}
async function tick(){try{const s=await (await fetch('/state')).json();
const ev=s.events||[],la=ev.length?ev[ev.length-1]:null,cf=s.config||{};let md='IDLE';
if(s.status==='running'||la){if(!la)md='NORMAL';else if(la.action==='BACKOFF')md='DEGRADED';else if(la.action==='RECOVER')md='RECOVERING';else if(la.action==='FIXED')md='FIXED';else md=(cf.start_conc&&la.next_conc<cf.start_conc)?'RECOVERING':'NORMAL'}
if(s.aborted)md='DOWN';
const mb=$('mode');mb.textContent=md==='FIXED'?'FIXED (agent off)':md;mb.className='tag '+md;
const c=s.conn||{};$('sub').textContent='LIVE | model '+(c.model||s.model)+' | endpoint '+s.endpoint+' (your GPU on Akamai) | connection: '+(c.ok?'VERIFIED by a real test call ('+c.ms+' ms)':'NOT CONNECTED - '+(c.err||''));
$('calls').textContent=s.calls;$('avg').textContent=s.calls?Math.round(s.call_ms_sum/s.calls):'-';
$('prog').textContent=s.done+'/'+s.total;$('rt').textContent=s.route_model+' / '+s.route_fallback;
$('st').textContent=s.status==='running'?'running...':(s.status==='done'?'done in '+s.wall_s+'s':'');$('go').disabled=s.status==='running';
$('tb').innerHTML=s.events.map(e=>`<tr><td>${e.wave}</td><td>${e.conc}</td><td>${e.p50}</td><td>${e.p95}</td><td>${e.errors}</td><td>${e.running??'-'}</td><td>${e.waiting??'-'}</td><td><span class="tag ${e.action}">${e.action}</span></td><td>${e.next_conc}</td><td>${e.deep_tokens}</td></tr>`).join('');
$('feed').innerHTML='';s.recent.forEach(r=>{const d=document.createElement('div');const t=document.createElement('span');t.className='t';t.textContent=r.task;const m=document.createElement('span');m.textContent=r.label+' '+r.ms+'ms'+(r.ok?'':' ERR');d.append(t,m);$('feed').append(d)});
$('lg').innerHTML=(s.log&&s.log.length)?s.log.slice().reverse().map(l=>`<div><span style="color:var(--mu)">${l.t}</span><span class="t" style="white-space:normal"></span></div>`).join(''):$('lg').innerHTML;
if(s.log&&s.log.length){[...$('lg').children].forEach((d,i)=>{d.lastChild.textContent=s.log[s.log.length-1-i].msg})}
$('hb').innerHTML=(s.history||[]).map(h=>`<tr><td>${h.mode}</td><td>${h.start_conc}</td><td>${h.tasks}</td><td>${h.wall_s}</td><td>${h.tps}</td><td>${h.p50}</td><td>${h.p95}</td><td>${h.over_slo}%</td><td>${h.backoffs}</td></tr>`).join('');
draw(s.events,(s.config&&s.config.slo_ms)||+$('slo').value)}catch(e){$('sub').textContent='waiting for server...'}}
$('ask').onclick=async()=>{const b=$('ask');b.disabled=true;$('ans').textContent='thinking...';try{const r=await (await fetch('/ask',{method:'POST',body:JSON.stringify({text:$('q').value})})).json();$('ans').textContent=r.ok?`[route: ${r.label} (chosen by ${r.route_src}) | ${r.ms} ms | output budget ${r.max_tokens} tokens | tokens in/out: ${r.prompt_tokens ?? 'n/a'} / ${r.completion_tokens ?? 'n/a'}]\n\n${r.text}`:'FAILED: '+(r.error||'unknown')}catch(e){$('ans').textContent='FAILED: '+e}b.disabled=false};
setInterval(tick,1000);tick();
