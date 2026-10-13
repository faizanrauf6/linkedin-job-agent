import {useEffect,useState} from 'react';
import {api,json} from './api';
import {MailConnection} from './MailConnection';
import {Callout,External,Field,formatTime} from './components';
import type {Job,Run} from './types';

interface Attempt {id:string;started_at:string;finished_at:string;state:string;adapter:string;version:string;error_class:string;reason:string}
interface Receipt {id:string;created_at:string;observed_at:string;text:string;reference:string;url:string;source:string}
interface StageRecord {id:string;created_at:string;stage:string;note:string;evidence_id:string}
interface EmailEvidence {id:string;created_at:string;subject:string;sender:string;date:string;text:string;classification:string;suggested_stage:string;company_match:boolean;role_match:boolean}
interface History {job_id:string;stage:string;stages:string[];attempts:Attempt[];receipts:Receipt[];stage_history:StageRecord[];email_evidence:EmailEvidence[]}

const stageLabel=(value:string)=>value==='prospect'?'Considering':value==='applied'?'Applied · tracking only':value.charAt(0).toUpperCase()+value.slice(1);

export function Tracking({job,run,refresh}:{job:Job;run:Run;refresh:()=>Promise<void>}){
 const [history,setHistory]=useState<History|null>(null),[stage,setStage]=useState('prospect'),[note,setNote]=useState(''),[error,setError]=useState('');
 const [file,setFile]=useState<File|null>(null),[consent,setConsent]=useState(false),[fileKey,setFileKey]=useState(0);
 const path='/jobs/'+encodeURIComponent(job.id);
 const load=async()=>{const next=await api<History>(path+'/history');setHistory(next);setStage(next.stage);setError('')};
 useEffect(()=>{
  let active=true;setHistory(null);setNote('');setFile(null);setConsent(false);setError('');setFileKey(value=>value+1);
  api<History>('/jobs/'+encodeURIComponent(job.id)+'/history').then(next=>{if(active){setHistory(next);setStage(next.stage)}}).catch(cause=>{if(active)setError(cause instanceof Error?cause.message:'History could not load')});
  return()=>{active=false};
 },[job.id,job.status]);
 const saveStage=(value:string,evidenceId='')=>run(async()=>{
  await api(path+'/stage',json('POST',{stage:value,note,evidence_id:evidenceId,confirmation:true}));setNote('');await load();await refresh();
 });
 const importEmail=()=>{
  if(!file||!consent)return;
  const body=new FormData();body.set('file',file);body.set('consent','true');
  run(async()=>{await api(path+'/email-evidence',{method:'POST',body});setFile(null);setConsent(false);setFileKey(value=>value+1);await load();await refresh()});
 };
 const exportPath='/api/tracking/export?job_id='+encodeURIComponent(job.id)+'&format=';
 return <section className="drawer-section" aria-label="Application history and tracking"><h3>Application history &amp; next steps</h3><p>Track recruiter progress separately from submission status. A stage change never queues or submits an application.</p>
  {error&&<Callout>{error}<button className="button secondary" onClick={()=>run(load)}>Reload history</button></Callout>}
  {!history&&!error&&<p role="status">Loading history…</p>}
  {history&&<>
   <form onSubmit={event=>{event.preventDefault();saveStage(stage)}}><Field label="Recruiter pipeline stage"><select value={stage} onChange={event=>setStage(event.target.value)}>{history.stages.map(value=><option key={value} value={value}>{stageLabel(value)}</option>)}</select></Field><Field label="Tracking note"><textarea maxLength={1000} value={note} onChange={event=>setNote(event.target.value)} placeholder="What happened next?"/></Field><button className="button secondary" type="submit">Save tracking stage</button></form>
   <div className="drawer-links"><a href={exportPath+'json'} download>Export this history as JSON</a><a href={exportPath+'csv'} download>Export this history as CSV</a></div>
   <h4>Application attempts</h4>{history.attempts.length?<ol>{history.attempts.map(attempt=><li key={attempt.id}><strong>{attempt.state.replaceAll('_',' ')}</strong> · {formatTime(attempt.started_at)}<p>{attempt.adapter||'Adapter not recorded'}{attempt.version?' · version '+attempt.version:''}{attempt.error_class?' · '+attempt.error_class:''}</p>{attempt.reason&&<p>{attempt.reason}</p>}{!attempt.finished_at&&<small>This attempt has no recorded finish. Check the application before retrying.</small>}</li>)}</ol>:<p>No attempts recorded yet.</p>}
   <h4>Observed submission receipts</h4>{history.receipts.length?<ul>{history.receipts.map(receipt=><li key={receipt.id}><strong>{receipt.reference||'Browser confirmation'}</strong> · {formatTime(receipt.observed_at||receipt.created_at)}<p>{receipt.text}</p>{receipt.url&&<External url={receipt.url}>Open confirmation location</External>}</li>)}</ul>:<p>No browser receipt has been recorded. Imported emails do not count as observed submission receipts.</p>}
   <details><summary>Pipeline changes ({history.stage_history.length})</summary>{history.stage_history.length?<ol>{history.stage_history.map(item=><li key={item.id}>{stageLabel(item.stage)} · {formatTime(item.created_at)}{item.note&&<p>{item.note}</p>}{item.evidence_id&&<small>Accepted from imported email evidence.</small>}</li>)}</ol>:<p>No pipeline changes yet.</p>}</details>
   <h4>Optional email evidence</h4><p>Import an .eml file for this exact opportunity. Email text is unverified evidence and only suggests a stage for you to accept. You can also configure explicit, read-only mailbox sync below.</p>
   <Field label="Email file (.eml, up to 1 MB)"><input key={fileKey} type="file" accept=".eml,message/rfc822" onChange={event=>{setFile(event.target.files?.[0]||null);setConsent(false)}}/></Field>
   <label className="choice"><input type="checkbox" checked={consent} onChange={event=>setConsent(event.target.checked)}/>I authorize importing this email and storing its text with this opportunity.</label><button className="button secondary" disabled={!file||!consent||file.size>1024*1024} onClick={importEmail}>Import email evidence</button>{file&&file.size>1024*1024&&<Callout>Choose an email file no larger than 1 MB.</Callout>}
   {history.email_evidence.map(email=><article className="answer-record" key={email.id}><strong>{email.subject}</strong><p>{email.sender} · imported {formatTime(email.created_at)}</p><p>{email.classification==='receipt'?'Possible receipt':email.classification==='rejection'?'Possible rejection':email.classification==='interview'?'Possible interview':'Manual review required'} · {email.company_match&&email.role_match?'Employer and role mentioned; sender is unverified':'Employer or role does not match; keep as manual evidence'}</p><details><summary>Read imported plain text</summary><p className="job-description">{email.text||'No plain-text message body was present. HTML and attachments were not imported.'}</p></details>{email.suggested_stage&&<button className="button secondary" onClick={()=>saveStage(email.suggested_stage,email.id)}>Accept {stageLabel(email.suggested_stage).toLowerCase()} suggestion</button>}</article>)}
   <MailConnection job={job} run={run} refresh={async()=>{await load();await refresh()}}/>
  </>}
 </section>;
}
