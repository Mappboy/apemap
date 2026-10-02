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
