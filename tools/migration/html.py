"""Standalone architecture editor: exports a plan, never pretends to save state."""
import json
from pathlib import Path

from .core import draft_plan, report


def write_report(project, state, path=None):
    plan = state['architecture'].get('plan') or draft_plan(state)
    data = {'plan': plan, 'functions': state['inventory']['functions'], 'report': report(project, state)}
    path = Path(path) if path is not None else project / '.ctrust' / 'reports' / 'architecture.html'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(PAGE.replace('__DATA__', json.dumps(data, ensure_ascii=False).replace('<', '\\u003c')), encoding='utf-8')
    return path


PAGE = r'''<!doctype html>
<html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>ctrust — Arquitetura e progresso</title>
<style>body{font:16px system-ui;margin:2rem;max-width:1500px;color:#17212f;background:#f7f8fa}button,input,select{font:inherit;padding:.4rem;margin:.2rem}table{border-collapse:collapse;background:white;width:100%}th,td{border:1px solid #d5dce4;padding:.5rem;text-align:left;vertical-align:top}code{overflow-wrap:anywhere}header{position:sticky;top:0;background:#f7f8fa;padding:1rem 0}small{display:block}pre{white-space:pre-wrap}#errors{color:#9a251b}details{margin:1rem 0}</style>
<h1>Arquitetura da migração</h1>
<p id="revision"></p><p>Este arquivo representa uma revisão. Exporte o plano e valide/aplique pelo CLI; alterações nesta página não atualizam o projeto.</p>
<details><summary>Progresso e próxima ação desta revisão</summary><pre id="progress"></pre></details>
<header>
<input id="search" placeholder="Buscar função, arquivo ou slice" aria-label="Buscar">
<select id="filter" aria-label="Filtrar"><option value="all">Todas</option><option value="shared">Compartilhadas</option><option value="pending">Sem slice</option><option value="uncertain">Não resolvidas</option></select>
<button id="select">Selecionar visíveis</button><button id="add">Criar/editar slice</button><button id="remove">Remover slice vazio</button><button id="batch">Atribuir selecionadas</button><button id="decision">Tratar selecionadas</button>
<button id="download">Exportar plano JSON</button><label>Reabrir plano JSON <input id="upload" type="file" accept=".json"></label>
</header><pre id="errors" role="status"></pre><div id="slices"></div>
<table><thead><tr><th>Selecionar</th><th>Função / origem</th><th>Dependências / consumidores</th><th>Tratamento</th><th>Slice responsável</th><th>Nome Rust / justificativa</th></tr></thead><tbody id="rows"></tbody></table>
<script type="application/json" id="data">__DATA__</script>
<script>
'use strict';
const data=JSON.parse(document.getElementById('data').textContent);
let plan=structuredClone(data.plan); const selected=new Set();let visible=[];
const el=id=>document.getElementById(id);
el('revision').textContent='Schema 3 · revisão '+data.report.revision+' · inventário '+plan.inventory;
el('progress').textContent=JSON.stringify(data.report,null,2);
const callers={};for(const [id,fn] of Object.entries(data.functions))for(const target of fn.calls||[])(callers[target]??=[]).push(id);
const shared=new Map(data.report.shared_candidates.map(item=>[item.function,item.roots]));
function validate(){
 const errors=[], deps={};for(const sid of Object.keys(plan.slices))deps[sid]=new Set();
 for(const item of data.report.unresolved_includes||[]){if(!item.include.startsWith('<')){const key=item.file+'::'+item.include;if(!plan.include_decisions?.[key]?.reason)errors.push('Header ausente '+key+': forneça o arquivo/refaça inventário ou registre include_decisions no plano');}}
 for(const [id,a] of Object.entries(plan.assignments)){
  if(a.role==='port'){
   if(!plan.slices[a.slice])errors.push(data.functions[id].label+': selecione um slice');
   if(data.functions[id].status!=='resolved')errors.push(data.functions[id].label+': resolução pendente');
   if((data.functions[id].indirect_calls||[]).length&&!a.indirect_decision)errors.push(data.functions[id].label+': chamadas indiretas exigem revisão no plano');
   for(const child of data.functions[id].calls||[]){const other=plan.assignments[child];if(other?.role==='exclude')errors.push(data.functions[id].label+': dependência excluída '+data.functions[child].label);if(other?.role==='port'&&deps[a.slice]&&other.slice!==a.slice)deps[a.slice].add(other.slice);}
  }else if(!a.reason?.trim())errors.push(data.functions[id].label+': justifique '+a.role);
 }
 for(const sid of Object.keys(plan.slices))if(!Object.values(plan.assignments).some(a=>a.role==='port'&&a.slice===sid))errors.push(sid+': slice sem funções próprias; reagrupe ou remova o slice');
 const visited=new Set(),active=new Set();function visit(s){if(active.has(s)){errors.push('Ciclo entre slices em '+s);return;}if(visited.has(s)||!deps[s])return;active.add(s);for(const d of deps[s])visit(d);active.delete(s);visited.add(s);}for(const s of Object.keys(deps))visit(s);
 el('errors').textContent=errors.length?errors.join('\n'):'Sem pendências locais. O controlador fará a validação completa antes da confirmação.';
 el('slices').textContent='Slices: '+Object.entries(plan.slices).map(([id,s])=>id+' → '+s.crate+'/src/'+s.module+'.rs').join(' | ');
 return errors;
}
function input(value,change){const x=document.createElement('input');x.value=value||'';x.onchange=()=>{change(x.value);validate();};return x;}
function render(){
 el('rows').replaceChildren();visible=[];const query=el('search').value.toLowerCase(),filter=el('filter').value;
 for(const [id,fn] of Object.entries(data.functions)){
  const a=plan.assignments[id], consumers=callers[id]||[];
  if(![id,fn.label,fn.file||'',a.slice||''].join(' ').toLowerCase().includes(query))continue;
  if(filter==='shared'&&!shared.has(id)||filter==='pending'&&(a.role!=='port'||a.slice)||filter==='uncertain'&&fn.status==='resolved')continue;
  visible.push(id);
  const row=document.createElement('tr');function cell(){const c=document.createElement('td');row.append(c);return c;}
  const check=document.createElement('input');check.type='checkbox';check.checked=selected.has(id);check.onchange=()=>check.checked?selected.add(id):selected.delete(id);cell().append(check);
  const origin=cell();origin.textContent=fn.label;const small=document.createElement('small');small.textContent=(fn.file||fn.status)+':'+(fn.line||'')+' · '+id;origin.append(small);
  const links=cell();links.textContent='Chama: '+(fn.calls||[]).map(c=>data.functions[c].label).join(', ')+' | Consumidores: '+consumers.map(c=>data.functions[c].label).join(', ');
  if(shared.has(id)){const hint=document.createElement('small');hint.textContent='Compartilhada por '+shared.get(id).length+' raízes: considere um slice coeso, sem duplicar implementação.';links.append(hint);}
  const role=document.createElement('select');for(const name of ['port','adapt','simulate','exclude']){const o=document.createElement('option');o.value=name;o.textContent=name;role.append(o);}role.value=a.role;role.onchange=()=>{a.role=role.value;render();};cell().append(role);
  const owner=document.createElement('select');for(const name of ['',...Object.keys(plan.slices)]){const o=document.createElement('option');o.value=name;o.textContent=name||'Escolher slice';owner.append(o);}owner.value=a.slice||'';owner.disabled=a.role!=='port';owner.onchange=()=>{a.slice=owner.value;validate();};cell().append(owner);
  const edits=cell();edits.append(input(a.rust_name,v=>a.rust_name=v),input(a.reason,v=>a.reason=v));
  el('rows').append(row);
 }
 validate();
}
el('search').oninput=render;el('filter').onchange=render;
el('select').onclick=()=>{const clear=visible.every(id=>selected.has(id));for(const id of visible)clear?selected.delete(id):selected.add(id);render();};
el('add').onclick=()=>{const id=prompt('ID do slice (ou ID existente para editar)');if(!id)return;const old=plan.slices[id]||{};const crate=prompt('Crate relativo à raiz do projeto',old.crate||'crates/domain');if(crate===null)return;const module=prompt('Módulo Rust',old.module||id.replaceAll('-','_'));if(module===null)return;plan.slices[id]={crate,module};render();};
el('batch').onclick=()=>{const sid=prompt('Slice para as '+selected.size+' funções selecionadas');if(!sid)return;if(!plan.slices[sid]){alert('Crie o slice primeiro');return;}for(const id of selected){plan.assignments[id].slice=sid;plan.assignments[id].role='port';}render();};
el('remove').onclick=()=>{const sid=prompt('Slice vazio a remover');if(!sid)return;if(Object.values(plan.assignments).some(a=>a.role==='port'&&a.slice===sid)){alert('Reatribua as funções antes de remover o slice');return;}delete plan.slices[sid];render();};
el('decision').onclick=()=>{const role=prompt('Tratamento para selecionadas: adapt, simulate ou exclude','exclude');if(!['adapt','simulate','exclude'].includes(role))return;const reason=prompt('Justificativa da decisão');if(!reason?.trim())return;for(const id of selected){plan.assignments[id].role=role;plan.assignments[id].reason=reason;plan.assignments[id].slice='';}render();};
el('download').onclick=()=>{validate();const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(plan,null,2)],{type:'application/json'}));a.download='architecture.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);};
el('upload').onchange=async()=>{try{const candidate=JSON.parse(await el('upload').files[0].text());if(candidate.version!==3||candidate.inventory!==data.plan.inventory||!candidate.slices||!candidate.assignments||Object.keys(candidate.assignments).sort().join()!==Object.keys(data.functions).sort().join())throw Error('Schema, inventário ou funções incompatíveis');plan=candidate;render();}catch(e){el('errors').textContent='Plano recusado: '+e.message;}};
render();
</script></html>'''
