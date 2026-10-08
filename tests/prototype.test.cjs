const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const context={URLSearchParams};vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../apemap/prototype_assets/explorer.js'),'utf8'),context);
const api=context.ApemapExplorer;
const schools={g:{institution_id:'g',school_name:'Government school',school_sector:'Government',coordinates:null},i:{institution_id:'i',school_name:'Independent school',school_sector:'Independent',coordinates:[150,-30]}};
const person=(id,party,chamber,relations)=>({member_id:id,display_name:id,party_abbrev:party,chamber,schools:relations.map(institution_id=>({institution_id}))});
const payload={parliaments:[47,48],schools,members:{47:[person('Changing member','ALP','senate',['g','g','i']),person('No schooling','IND','senate',[])],48:[person('Changing member','IND','representatives',['g','i'])]}};
test('party and chamber belong to the selected parliament',()=>{
  const state=api.parseState(payload,'#parliament=47&party=ALP&chamber=senate');
  const results=api.select(payload,state);assert.equal(results.schools.length,2);assert.equal(results.schools[0].members.length,1);
  assert.equal(api.select(payload,{...state,parliament:48}).schools.length,0);
});
test('unmapped schools and missing schooling remain accessible',()=>{
  const results=api.select(payload,api.parseState(payload,''));assert.equal(results.schools.length,2);
  const earlier=api.select(payload,api.parseState(payload,'#parliament=47'));assert.equal(earlier.members.length,2);assert.equal(earlier.schools.find(s=>s.institution_id==='g').coordinates,null);
});
test('school search, member search and school sectors have separate semantics',()=>{
  const state=api.parseState(payload,'#parliament=47&sector=Independent&q=changing');
  assert.equal(api.select(payload,state).schools.length,1);
  assert.equal(api.select(payload,{...state,q:'government'}).schools.length,0);
  assert.equal(api.select(payload,{...state,sector:'',q:'government'}).schools.length,1);
});
test('URL state roundtrips and invalid options reset',()=>{
  const state=api.parseState(payload,'#parliament=47&q=A%26B&school=g');
  assert.equal(api.parseState(payload,api.encodeState(state)).q,'A&B');
  const invalid=api.parseState(payload,'#parliament=999&sector=Mixed&party=missing&chamber=invalid');
  assert.equal(invalid.parliament,48);assert.equal(invalid.sector,'');assert.equal(invalid.party,'');assert.equal(invalid.chamber,'');
});

test('successor names, original names and recorded aliases find distinct attended schools',()=>{
  const shared={resolved_institution_id:'current',resolved_institution_name:'Current College',is_successor:true};
  const contextual={
    a:{...shared,institution_id:'a',school_name:'Original A',attended_school_name:'Original A',display_school_name:'Original A → Current College*',school_sector:'Other',coordinates:[141,-40],location_basis:'original_verified'},
    b:{...shared,institution_id:'b',school_name:'Original B',attended_school_name:'Original B',display_school_name:'Original B → Current College*',school_sector:'Other',coordinates:[150,-35],location_basis:'successor_unverified'},
    missing:{institution_id:'missing',school_name:'Missing location',coordinates:null,school_sector:'Other'}
  };
  const member=person('Attendee','TST','senate',['a','a','b','missing']);
  member.schools[0].school_name_as_recorded='Old alias spelling';
  const fixture={parliaments:[47],schools:contextual,members:{47:[member]}};
  const state=api.parseState(fixture,'#parliament=47&q=current');
  assert.deepEqual(Array.from(api.select(fixture,state).schools,s=>s.institution_id),['a','b']);
  assert.equal(api.select(fixture,{...state,q:'old alias'}).schools[0].institution_id,'a');
  assert.equal(api.select(fixture,{...state,q:'missing'}).schools[0].coordinates,null);
  assert.equal(api.select(fixture,{...state,q:''}).schools[0].members.length,1);
  assert.equal(api.schoolTitle(contextual.b),'Original B → Current College*');
  assert.equal(api.markerStyle(contextual.a).fill,'#2f7142');
  assert.equal(api.markerStyle(contextual.b).fill,'none');
  assert.equal(api.markerStyle({...contextual.b,location_basis:'successor_verified_same_campus'}).fill,'#2f7142');
});
