import {useCallback,useEffect,useState} from 'react';
import {Download,FileText,RefreshCw} from 'lucide-react';
import {api,json} from './api';
import {Callout,Field} from './components';
import type {Job,Run} from './types';

type Draft={
 id:string;kind:'cover_letter'|'cv_tailoring';title:string;text:string;
 source_fingerprint:string;reviewed:boolean;current:boolean;created_at:string;
 facts:{id:string;source:string;text:string}[];sources:string[];
};

export function WrittenDocuments({job,run,refresh}:{job:Job&{resume_hash?:string};run:Run;refresh?:()=>Promise<void>}){
 const [kind,setKind]=useState<'cover_letter'|'cv_tailoring'>('cover_letter');
 const [maxLength,setMaxLength]=useState(4000),[drafts,setDrafts]=useState<Draft[]>([]);
 const [checked,setChecked]=useState<Record<string,boolean>>({}),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const load=useCallback(async()=>{setDrafts(await api<Draft[]>('/jobs/'+job.id+'/drafts'))},[job.id]);
 useEffect(()=>{
  let active=true;
  setDrafts([]);setChecked({});setError('');
  api<Draft[]>('/jobs/'+job.id+'/drafts').then(data=>{if(active)setDrafts(data)}).catch(e=>{if(active)setError(e.message)});
  return()=>{active=false};
 },[job.id,job.resume_id,job.resume_hash]);
 const perform=(work:()=>Promise<void>)=>run(async()=>{
  setBusy(true);setError('');
  try{await work()}
  catch(e){setError(e instanceof Error?e.message:'The written draft could not be updated.');await load().catch(()=>{});throw e}
  finally{setBusy(false)}
 });
 const generate=()=>perform(async()=>{
  await api<Draft>('/jobs/'+job.id+'/drafts',json('POST',{kind,max_length:maxLength}));
  setChecked({});await load();await refresh?.();
 });
 const review=(draft:Draft)=>perform(async()=>{
  const reviewed=await api<Draft>('/drafts/'+draft.id+'/review',json('POST',{confirmation:true,source_fingerprint:draft.source_fingerprint}));
  setDrafts(items=>items.map(item=>item.id===draft.id?reviewed:item));
 });
 const download=(draft:Draft)=>perform(async()=>{
  const response=await fetch('/api/drafts/'+draft.id+'/download',{credentials:'same-origin',headers:{Accept:'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}});
  if(!response.ok){const data=await response.json();throw new Error(typeof data.detail==='string'?data.detail:'The draft could not be downloaded.')}
  const url=URL.createObjectURL(await response.blob());
  const link=document.createElement('a');link.href=url;link.download=draft.kind.replace('_','-')+'-'+draft.id+'.docx';
  document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
 });
 return <section className="drawer-section" aria-label="Written application documents">
  <h3><FileText size={17}/>Written documents</h3>
  <p>Create a draft from your confirmed profile and selected CV. Review every claim and its source before downloading.</p>
  <p>CV tailoring creates a separate draft. Your original CV and the CV selected for this application stay unchanged.</p>
  {!job.resume_id&&<Callout>Prepare this opportunity with a confirmed CV before creating a written draft.</Callout>}
  <div className="form-grid">
   <Field label="Document type"><select value={kind} onChange={e=>setKind(e.target.value as typeof kind)} disabled={busy}>
    <option value="cover_letter">Cover letter</option><option value="cv_tailoring">CV tailoring draft</option>
   </select></Field>
   <Field label="Maximum characters"><input type="number" min={1} max={20000} value={maxLength} onChange={e=>setMaxLength(Number(e.target.value))} disabled={busy}/></Field>
  </div>
  <div className="drawer-actions">
   <button className="button secondary" disabled={busy||!job.resume_id||!Number.isInteger(maxLength)||maxLength<1||maxLength>20000} onClick={generate}>Create draft</button>
   <button className="button secondary" disabled={busy} onClick={()=>perform(load)}><RefreshCw size={14}/>Refresh draft status</button>
  </div>
  {error&&<div role="alert"><Callout>{error}</Callout></div>}
  {drafts.map(draft=><article className="answer-record" key={draft.id}>
   <h4>{draft.title}</h4>
   <p>{draft.reviewed?'Reviewed':'Review required'} · {draft.text.length} characters</p>
   {!draft.current&&<Callout>The source profile, role, settings, or CV changed. Create and review a new draft before downloading.</Callout>}
   <pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere',fontFamily:'inherit',fontSize:'inherit'}}>{draft.text}</pre>
   <details><summary>Facts and sources ({draft.facts.length})</summary>
    {draft.facts.map(fact=><div className="answer-record" key={fact.id}>
     <p style={{whiteSpace:'pre-wrap'}}>{fact.text}</p><small style={{overflowWrap:'anywhere'}}>{fact.source}</small>
    </div>)}
   </details>
   {!draft.reviewed&&draft.current&&<>
    <label className="checkbox-row"><input type="checkbox" checked={!!checked[draft.id]} onChange={e=>setChecked(values=>({...values,[draft.id]:e.target.checked}))} disabled={busy}/>I reviewed this draft and its source facts.</label>
    <button className="button secondary" disabled={busy||!checked[draft.id]} onClick={()=>review(draft)}>Confirm reviewed draft</button>
   </>}
   <button className="button secondary" disabled={busy||!draft.reviewed||!draft.current} onClick={()=>download(draft)}><Download size={14}/>Download reviewed DOCX</button>
  </article>)}
 </section>;
}
