/* Shared, offline reference behaviour. No analytical classification runs here. */
(() => {
  'use strict';
  const sectors = ['Government', 'Catholic', 'Independent', 'Other'];
  const normal = value => String(value || '').normalize('NFKC').toLocaleLowerCase('en-AU').trim();
  const schoolSector = school => sectors.includes(school.school_sector) ? school.school_sector : 'Other';
  function parseState(payload, hash) {
    const params = new URLSearchParams(hash.replace(/^#/, ''));
    const parliament = payload.parliaments.includes(Number(params.get('parliament'))) ? Number(params.get('parliament')) : Math.max(...payload.parliaments);
    const members = payload.members[String(parliament)];
    const valid = (key, choices) => choices.includes(params.get(key)) ? params.get(key) : '';
    return {parliament, sector:valid('sector', sectors), party:valid('party', members.map(m => m.party_abbrev || m.party || 'Unknown')), chamber:valid('chamber', members.map(m => m.chamber || 'Unknown')), q:params.get('q') || '', school:params.get('school') || ''};
  }
  function encodeState(state) {
    const params = new URLSearchParams({parliament:String(state.parliament)});
    for (const key of ['sector','party','chamber','q','school']) if (state[key]) params.set(key, state[key]);
    return '#' + params.toString();
  }
  function select(payload, state) {
    const query = normal(state.q);
    const matchingMembers = payload.members[String(state.parliament)].filter(member =>
      (!state.party || (member.party_abbrev || member.party || 'Unknown') === state.party) &&
      (!state.chamber || (member.chamber || 'Unknown') === state.chamber));
    const schools = new Map();
    const people = [];
    for (const member of matchingMembers) {
      const memberMatch = normal(member.display_name).includes(query);
      let matched = false;
      for (const relation of member.schools) {
        const school = payload.schools[relation.institution_id];
        if (!school || (state.sector && schoolSector(school) !== state.sector)) continue;
        if (query && !memberMatch && !normal(school.school_name).includes(query)) continue;
        matched = true;
        if (!schools.has(school.institution_id)) schools.set(school.institution_id, {...school, members:new Map()});
        schools.get(school.institution_id).members.set(member.member_id, member);
      }
      if (matched || (!state.sector && !member.schools.length && (!query || memberMatch))) people.push(member);
    }
    return {schools:[...schools.values()].map(s => ({...s,members:[...s.members.values()]})).sort((a,b) => a.school_name.localeCompare(b.school_name,'en-AU')), members:people};
  }
  globalThis.ApemapExplorer = {parseState, encodeState, select};
  if (typeof document === 'undefined') return;
  const payload = JSON.parse(document.getElementById('explorer-data').textContent);
  const form = document.getElementById('filters');
  let state = parseState(payload, location.hash);
  let failed = false;
  const el = (tag, text, parent) => {const node=document.createElement(tag); if(text!==undefined) node.textContent=text; if(parent) parent.append(node); return node;};
  function optionList(name, values) {
    const field=form.elements.namedItem(name); field.replaceChildren();
    const all=el('option','All '+name,field); all.value='';
    for(const value of [...new Set(values)].sort()) {const option=el('option',value,field);option.value=value;}
  }
  function drawLocator(schools) {
    const host=document.getElementById('locator-content');host.replaceChildren();
    if(failed) {el('p','The locator is unavailable. Use the school list and details.',host);return;}
    const points=schools.filter(s=>s.coordinates);
    if(!points.length) {el('p','No matching schools have usable coordinates. All matches remain in the list.',host);return;}
    const ns='http://www.w3.org/2000/svg';const svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 500 280');svg.setAttribute('role','img');svg.setAttribute('aria-label','Matching school coordinates. Select schools through the adjacent list.');
    const lons=points.map(s=>s.coordinates[0]), lats=points.map(s=>s.coordinates[1]);
    const west=Math.min(...lons)-1,east=Math.max(...lons)+1,south=Math.min(...lats)-1,north=Math.max(...lats)+1;
    for(const school of points) {const circle=document.createElementNS(ns,'circle');circle.setAttribute('cx',String(12+(school.coordinates[0]-west)/(east-west)*476));circle.setAttribute('cy',String(12+(north-school.coordinates[1])/(north-south)*256));circle.setAttribute('r',school.institution_id===state.school?'6':'3');svg.append(circle);}
    host.append(svg);el('p',`${points.length} mapped of ${schools.length} matching schools. Bounds: ${west.toFixed(1)}–${east.toFixed(1)}° longitude, ${south.toFixed(1)}–${north.toFixed(1)}° latitude.`,host);
  }
  function render(writeHash=true) {
    const cohort=payload.members[String(state.parliament)];
    optionList('party',cohort.map(m=>m.party_abbrev||m.party||'Unknown'));optionList('chamber',cohort.map(m=>m.chamber||'Unknown'));
    if(!cohort.some(m=>(m.party_abbrev||m.party||'Unknown')===state.party))state.party='';
    if(!cohort.some(m=>(m.chamber||'Unknown')===state.chamber))state.chamber='';
    for(const key of ['parliament','sector','party','chamber','q']) form.elements.namedItem(key).value=state[key];
    const results=select(payload,state);
    if(state.school&&!results.schools.some(s=>s.institution_id===state.school))state.school='';
    document.getElementById('result-count').textContent=`${results.schools.length} schools · ${results.members.length} matching people · ${results.schools.filter(s=>!s.coordinates).length} schools without coordinates.`;
    const list=document.getElementById('school-list');list.replaceChildren();
    if(!results.schools.length)el('p','No schools match. Clear filters or change the search.',list);
    for(const school of results.schools) {const button=el('button',school.school_name,list);button.type='button';button.className='school-button';button.setAttribute('aria-pressed',String(state.school===school.institution_id));el('small',`${schoolSector(school)} · ${school.state||'State unavailable'} · ${school.members.length} people${school.coordinates?'':' · Coordinates unavailable'}`,button);button.addEventListener('click',()=>{state.school=school.institution_id;render();document.getElementById('school-detail').focus();});}
    const people=document.getElementById('member-list');people.replaceChildren();
    for(const member of results.members) {const row=el('p',`${member.display_name} · ${member.party_abbrev||member.party||'Unknown'} · ${member.chamber} · ${member.education_classification}`,people);row.className='member-row';}
    const detail=document.getElementById('school-detail');detail.replaceChildren();detail.tabIndex=-1;
    const selected=results.schools.find(s=>s.institution_id===state.school);
    if(!selected)el('p','Select a school from the list.',detail);
    else {el('h4',selected.school_name,detail);el('p',`${schoolSector(selected)} · ${selected.state||'State unavailable'} · ${selected.coordinates?'Mapped':'Coordinates unavailable'}`,detail);el('p','Attendance does not equate to graduation.',detail);for(const member of selected.members)el('p',`${member.display_name} · ${member.party_abbrev||member.party} · ${member.chamber}`,detail);
      if(!selected.profile_year)el('p','Profile fields are unavailable in this explorer payload. Consult the research downloads for unmapped institutions.',detail);
      else {el('p',`School profile year ${selected.profile_year}; describes that year, not attendance-era conditions.`,detail);for(const [label,key] of [['ICSEA (context, not quality)','icsea'],['ICSEA percentile','icsea_percentile'],['Total enrolments','total_enrolments'],['SEA bottom quarter %','sea_bottom_quarter_pct'],['SEA lower-middle quarter %','sea_lower_middle_quarter_pct'],['SEA upper-middle quarter %','sea_upper_middle_quarter_pct'],['SEA top quarter %','sea_top_quarter_pct'],['Indigenous enrolment %','indigenous_enrolments_pct'],['LBOTE %','lbote_pct'],['Remoteness','remoteness_category']])el('p',`${label}: ${selected[key]??'Unavailable'}`,detail);}}
    if(document.getElementById('locator').open)drawLocator(results.schools);
    if(writeHash)history.replaceState(null,'',encodeState(state));
  }
  form.addEventListener('input',()=>{for(const key of ['parliament','sector','party','chamber','q'])state[key]=key==='parliament'?Number(form.elements.namedItem(key).value):form.elements.namedItem(key).value;render();});
  form.addEventListener('submit',event=>event.preventDefault());
  form.addEventListener('reset',event=>{event.preventDefault();state={parliament:Math.max(...payload.parliaments),sector:'',party:'',chamber:'',q:'',school:''};failed=false;render();});
  window.addEventListener('hashchange',()=>{state=parseState(payload,location.hash);render(false);});
  document.getElementById('locator').addEventListener('toggle',()=>{if(document.getElementById('locator').open)drawLocator(select(payload,state).schools);});
  document.getElementById('locator-failure').addEventListener('click',()=>{failed=!failed;document.getElementById('locator-failure').textContent=failed?'Restore locator':'Preview map unavailable';drawLocator(select(payload,state).schools);});
  form.hidden=false;document.getElementById('interactive').hidden=false;document.getElementById('static-schools').open=false;render();
})();
