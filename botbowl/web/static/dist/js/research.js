/* Research UI consumes copied public data; navigation never issues engine actions. */
'use strict';
(() => {
  const $ = id => document.getElementById(id);
  let library = [], canFork = false, limit = 32 * 1024 * 1024, token = '', selectedEntity = null;
  let generation = 0, playing = false, timer = null, interiorEvent = false, actions = [];
  const labels = {observed:'Observed', simulated_alternative:'Simulated continuation',
    model_prediction:'Model prediction', retrospective_analysis:'Retrospective analysis', human_annotation:'Human annotation'};
  const say = message => { $('status').textContent = message; };
  const node = (tag, text, className) => {
    const e = document.createElement(tag); if (text !== undefined) e.textContent = text;
    if (className) e.className = className; return e;
  };
  const showJSON = value => JSON.stringify(value, null, 2);
  const entry = id => library.find(row => row.id === id);
  const factual = () => entry($('factual').value);
  async function api(path, data) {
    const response = await fetch('/research/' + path, {cache:'no-store', headers:{
      Authorization:'Bearer ' + token, ...(data === undefined ? {} : {'Content-Type':'application/json'})},
      method:data === undefined ? 'GET' : 'POST', body:data === undefined ? undefined : JSON.stringify(data)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Request failed');
    return result;
  }
  function stop() {
    playing = false; clearTimeout(timer); $('play').textContent = 'Play navigation';
    $('play').setAttribute('aria-pressed', 'false');
  }
  const run = fn => async event => {if (event) event.preventDefault(); try {await fn(event);} catch (error) {stop(); say(error.message);}};
  function options(select, rows, first) {
    const old = select.value; select.replaceChildren(new Option(first, ''));
    rows.forEach(row => select.add(new Option(row.provenance.kind === 'observed' ? row.replay_id :
      `${row.provenance.initial_action.type} · horizon ${row.provenance.horizon} · ${row.id.slice(0,8)}`, row.id)));
    if (rows.some(row => row.id === old)) select.value = old;
  }
  function updateBranches() {
    const root = factual();
    const children = library.filter(row => root && row.provenance.parent === root.id);
    options($('branch-a'), children, 'None'); options($('branch-b'), children, 'None');
    $('turn').replaceChildren(new Option('Choose turn', ''));
    if (root) root.turns.forEach(t => $('turn').add(new Option(
      `Half ${t.half}, round ${t.round}, team turn ${t.team_turn_seq}`, String(t.decision_start))));
    $('load-actions').disabled = !canFork || !root || interiorEvent;
    $('fork').disabled = true; actions = []; $('action').replaceChildren();
  }
  async function refresh() {
    const result = await api('replays'); library = result.replays; canFork = result.can_fork;
    limit = result.max_bytes; $('horizon').max = String(result.max_horizon);
    options($('factual'), library.filter(row => row.provenance.kind === 'observed'), 'Open a replay');
    updateBranches();
  }
  function entityDetails(player, kind) {
    if (!player) return 'Absent entity';
    const position = player.position.present ? showJSON(player.position.value) : 'Missing data (off pitch)';
    return `${player.id} · ${player.team} · ${player.role}\nPosition: ${position}\n` +
      (kind === 'observed' ? 'Observed values:\n' : 'Simulated values:\n') + showJSON({attributes:player.attributes, status:player.status,
        location:player.location, skills:player.skills});
  }
  function panel(row, frame, requested, shared) {
    const p = node('article', undefined, 'panel'); p.dataset.replayId = row.id;
    p.append(node('h2', labels[row.provenance.kind] + ' · ' +
      (row.provenance.kind === 'observed' ? row.replay_id : row.provenance.initial_action.type), 'panel-heading'));
    const divergence = row.provenance.divergence;
    const phase = shared ? 'Shared factual prefix' : divergence === undefined ? 'Factual trajectory' :
      `Requested horizon: ${requested - divergence} decisions since divergence ${divergence}`;
    p.append(node('p', phase, 'alignment'));
    const exhausted = requested > row.final.decision_seq;
    p.append(node('p', exhausted ?
      `No data at requested decision ${requested}. Last available decision ${frame.context.decision_seq}; end: ${row.end.reason}` :
      'Available recorded state.', exhausted ? 'availability exhausted' : 'availability'));
    const ctx=frame.context, value=v=>v===null?'unavailable':String(v);
    p.append(node('p', `Decision ${ctx.decision_seq} · event ${ctx.event_seq}\nHalf ${value(ctx.half)} · round ${value(ctx.round)} · team turn ${value(ctx.team_turn_seq)}`, 'time'));
    const canvas = node('canvas'); const img = frame.image;
    canvas.width = img.width; canvas.height = img.height;
    canvas.setAttribute('role', 'img'); canvas.setAttribute('aria-label', `Geometric pitch, ${frame.observation.geometry.width} by ${frame.observation.geometry.height} cells; player controls follow`);
    const rgb = atob(img.rgb), rgba = new Uint8ClampedArray(img.width * img.height * 4);
    for (let i=0,j=0;i<rgb.length;i+=3,j+=4) {rgba[j]=rgb.charCodeAt(i);rgba[j+1]=rgb.charCodeAt(i+1);rgba[j+2]=rgb.charCodeAt(i+2);rgba[j+3]=255;}
    canvas.getContext('2d').putImageData(new ImageData(rgba,img.width,img.height),0,0); p.append(canvas);
    const buttons = node('div', undefined, 'entities'); buttons.setAttribute('aria-label', 'Players');
    frame.observation.players.forEach(player => {
      const b = node('button', player.id + ' · ' + player.role, player.id === selectedEntity ? 'selected' : '');
      b.dataset.entityId=player.id; b.setAttribute('aria-pressed', String(player.id === selectedEntity));
      b.addEventListener('click', run(async () => {selectedEntity=player.id; await render();})); buttons.append(b);
    });
    p.append(buttons, node('pre', selectedEntity ? entityDetails(frame.observation.players.find(v => v.id === selectedEntity), shared ? 'observed' : row.provenance.kind) : 'Select a player to inspect state values.', 'entity-detail'));
    const details=node('details');details.append(node('summary','Origin, action, policy, chance and full context'),
      node('pre',showJSON(frame.context),'context'),node('pre',showJSON({replay_id:row.replay_id,
        origin_family_id:row.origin_family_id,...row.provenance,end:row.end}),'provenance'));
    p.append(details);
    return p;
  }
  function records() {
    const root=factual(); $('records').replaceChildren();
    if (!root) return;
    [...root.predictions, ...root.annotations].forEach(record => {
      const section = node('article'); section.append(node('h3', labels[record.kind]));
      if(record.record){
        const r=record.record;section.append(node('p',`${r.model.model_id} · ${r.model.version} · issued at decision ${r.issued_at.decision_seq} · horizon ${r.horizon}`),node('pre',showJSON(r.output)));
        const details=node('details');details.append(node('summary','Emission, visible context and original record'),node('pre',showJSON(record)));section.append(details);
      }else{section.append(node('p',`Decision ${record.decision}`),node('p',record.text));}
      $('records').append(section);
    });
  }
  async function render() {
    const current=++generation, root=factual();
    $('fork').disabled=true; $('load-actions').disabled=true;
    if (!root) { $('panels').replaceChildren(); return; }
    const decision=Number($('decision').value);
    if (!Number.isSafeInteger(decision) || decision < root.initial.decision_seq) throw new Error('Choose a recorded decision');
    const rows=[root, entry($('branch-a').value), entry($('branch-b').value)].filter(Boolean);
    if (new Set(rows.map(r=>r.id)).size!==rows.length ||
        new Set(rows.slice(1).map(r=>r.provenance.divergence)).size>1) {
      $('panels').replaceChildren();
      throw new Error('Choose distinct alternatives from the same divergence decision');
    }
    if (decision > Math.max(...rows.map(r=>r.final.decision_seq))) throw new Error('No panel contains this decision');
    const panels=await Promise.all(rows.map(async row => {
      const shared=row !== root && decision <= row.provenance.divergence;
      const source=shared ? root : row;
      const bounded=Math.min(decision,source.final.decision_seq);
      const frame=await api(`replays/${source.id}/frame?decision=${bounded}`);
      return panel(row,frame,decision,shared);
    }));
    if (generation !== current) return;
    $('panels').replaceChildren(...panels); records();
    $('load-actions').disabled=!canFork || interiorEvent || decision > root.final.decision_seq;
    $('fork').disabled=true; actions=[]; $('action').replaceChildren();
    say('Connected · navigation is read-only');
  }
  async function navigate(value) {
    interiorEvent=false; $('boundary').textContent=''; $('event-detail').textContent='';
    $('decision').value=String(value); await render();
  }
  $('connect').addEventListener('submit',run(async () => {stop();say('Connecting…');token=$('token').value;await refresh();await render();say('Connected · navigation is read-only');}));
  $('upload').addEventListener('change',run(async () => {
    const file=$('upload').files[0]; if (!file) return;
    if (file.size>limit) throw new Error('Replay file too large');
    say('Validating replay…');const row=await api('replays',JSON.parse(await file.text())); await refresh();
    $('factual').value=row.id;updateBranches();await navigate(row.initial.decision_seq);
  }));
  $('factual').addEventListener('change',run(async()=>{stop();interiorEvent=false;selectedEntity=null;updateBranches();if(factual())await navigate(factual().initial.decision_seq);}));
  ['branch-a','branch-b'].forEach(id=>$(id).addEventListener('change',run(async()=>{stop();await render();})));
  $('go').addEventListener('click',run(async()=>{stop();await navigate(Number($('decision').value));}));
  $('previous').addEventListener('click',run(async()=>{stop();await navigate(Number($('decision').value)-1);}));
  $('next').addEventListener('click',run(async()=>{stop();await navigate(Number($('decision').value)+1);}));
  $('turn').addEventListener('change',run(async()=>{if($('turn').value){stop();await navigate(Number($('turn').value));}}));
  $('play').addEventListener('click',run(async()=>{
    if(playing){stop();return;} playing=true;$('play').textContent='Pause navigation';$('play').setAttribute('aria-pressed','true');
    const tick=run(async()=>{if(!playing)return;await navigate(Number($('decision').value)+1);if(playing)timer=setTimeout(tick,1000);});
    timer=setTimeout(tick,1000);
  }));
  $('search').addEventListener('click',run(async()=>{
    const root=factual();if(!root)return;
    const result=await api(`replays/${root.id}/events?kind=${encodeURIComponent($('kind').value)}&entity=${encodeURIComponent($('event-entity').value)}`);
    $('event').replaceChildren(new Option('Choose event',''));
    result.events.forEach(e=>$('event').add(new Option(`${e.context.event_seq} · ${e.kind} · decision ${e.decision_seq}`,String(e.context.event_seq))));
    say(`${result.events.length} events found`);
  }));
  $('event').addEventListener('change',run(async()=>{
    if(!$('event').value)return;stop();const root=factual();
    const result=await api(`replays/${root.id}/event?event=${$('event').value}`);
    interiorEvent=true;$('decision').value=String(result.previous_decision);await render();
    $('event-detail').textContent=showJSON(result.event);
    $('boundary').textContent=`Interior event ${result.event.context.event_seq}. Board is the preceding restorable decision ${result.previous_decision}; next boundary ${result.next_decision}. Use Go to decision to explicitly select a branch point.`;
  }));
  $('decision').addEventListener('input',()=>{stop();actions=[];$('fork').disabled=true;});
  $('load-actions').addEventListener('click',run(async()=>{
    const source=factual().id, decision=$('decision').value;
    const result=await api(`replays/${source}/actions?decision=${decision}`);
    if (!factual() || factual().id!==source || $('decision').value!==decision || interiorEvent) return;
    actions=result.actions;
    $('action').replaceChildren();actions.forEach((a,i)=>$('action').add(new Option(showJSON(a),String(i))));
    $('fork').disabled=!canFork||interiorEvent||!actions.length;
  }));
  $('fork').addEventListener('click',run(async()=>{
    stop();$('fork').disabled=true;say('Creating simulation…');const row=await api(`replays/${factual().id}/branches`,{decision:Number($('decision').value),action:actions[Number($('action').value)],horizon:Number($('horizon').value)});
    await refresh();$(!$('branch-a').value?'branch-a':'branch-b').value=row.id;await render();
  }));
  $('import-prediction').addEventListener('click',run(async()=>{await api(`replays/${factual().id}/predictions`,JSON.parse($('prediction').value));await refresh();await render();}));
  $('annotate').addEventListener('click',run(async()=>{await api(`replays/${factual().id}/annotations`,{decision:Number($('decision').value),text:$('annotation').value});await refresh();await render();}));
})();
