import {useEffect,useState} from 'react';
import {api,json} from './api';
import {Callout} from './components';
import type {Job,Run,Profile,Settings,Search} from './types';

type ReviewSnapshot={
 id:string;token:string;approved:boolean;created_at:string;
 payload:{
  job:{title:string;company:string;target:string};
  cv:{id:string;sha256:string;filename:string;label:string};
  answers:Record<string,string>;sources:Record<string,string>;employer_evidence:string[];
  configuration:{profile:Profile;settings:Omit<Settings,'paused'>;searches:Search[]};
  documents:{id:string;sha256:string;filename:string;kind:string}[];
 };
};

export function ApplicationReview({job,run,refresh}:{job:Job;run:Run;refresh:()=>Promise<void>}){
 const [snapshot,setSnapshot]=useState<ReviewSnapshot|null>(null);
 const [confirmed,setConfirmed]=useState(false);
 const [loading,setLoading]=useState(true);
 const [error,setError]=useState('');
 const [reload,setReload]=useState(0);
 useEffect(()=>{
  let active=true;
  setLoading(true);setConfirmed(false);setError('');setSnapshot(null);
  api<ReviewSnapshot>('/jobs/'+job.id+'/review').then(value=>{if(active)setSnapshot(value)}).catch(reason=>{if(active)setError(reason instanceof Error?reason.message:'Could not load application review.')}).finally(()=>{if(active)setLoading(false)});
  return()=>{active=false};
 },[job.id,job.status,job.resume_id,job.answers,job.answer_sources,job.evidence,reload]);
 const payload=snapshot?.payload;
 return <section className="drawer-section" aria-label="Application review">
  <h3>Review this application</h3>
  <p>Check the destination, selected attachments, answers, and their sources before approving this application.</p>
  {error&&<Callout>{error}</Callout>}
  {loading&&<p role="status">Loading application review…</p>}
  {payload&&<>
   <p><strong>{payload.job.title}</strong> at {payload.job.company}</p>
   <p>Destination: {payload.job.target||'Not available'}</p>
   <h4>Selected CV</h4>
   <p>{payload.cv.label} · {payload.cv.filename}</p>
   <details><summary>CV fingerprint</summary><code>{payload.cv.sha256}</code></details>
   <details><summary>Applicant and application settings</summary>
    <p>Applicant: {[payload.configuration.profile.first_name,payload.configuration.profile.last_name].filter(Boolean).join(' ')} · {payload.configuration.profile.email}</p>
    <p>Mode: {payload.configuration.settings.mode} · Daily application limit: {payload.configuration.settings.daily_limit}</p>
    <p>Operating hours: {payload.configuration.settings.operating_start}–{payload.configuration.settings.operating_end} ({payload.configuration.settings.timezone})</p>
    <p>Trusted employers: {Object.entries(payload.configuration.settings.trusted_employers).map(([employer,host])=>employer+': '+host).join('; ')||'None configured'}</p>
    <p>Approved sign-in hosts: {Object.entries(payload.configuration.settings.sso_hosts).map(([portal,hosts])=>portal+': '+hosts.join(', ')).join('; ')||'None configured'}</p>
    <h4>Searches and filters</h4>
    {payload.configuration.searches.length?<ul>{payload.configuration.searches.map(search=><li key={search.id}><strong>{search.name}</strong> ({search.enabled?'enabled':'disabled'})<p>Titles: {search.titles.join(', ')||'Any'} · Countries: {search.countries.join(', ')||'Any'} · Cities: {search.cities.join(', ')||'Any'} · Workplace: {search.workplaces.join(', ')||'Any'}</p><p>Required skills: {search.required_skills.join(', ')||'None'} · Minimum score: {search.min_score} · Posted within: {search.posted_within_days} days</p></li>)}</ul>:<p>No searches configured.</p>}
   </details>
   <h4>Employer evidence</h4>
   {payload.employer_evidence.length?<ul>{payload.employer_evidence.map((item,index)=><li key={index}>{item}</li>)}</ul>:<p>No employer evidence recorded.</p>}
   <h4>Prepared answers</h4>
   {Object.keys(payload.answers).length?<table><thead><tr><th>Question</th><th>Answer</th><th>Source</th></tr></thead><tbody>{Object.entries(payload.answers).map(([question,value])=><tr key={question}><th scope="row">{question}</th><td>{value}</td><td>{payload.sources[question]||'Source not recorded'}</td></tr>)}</tbody></table>:<p>No prepared answers recorded. Prepare the application to inspect its questions.</p>}
   <h4>Additional documents</h4>
   {payload.documents.length?<ul>{payload.documents.map(document=><li key={document.id}>{document.filename} · {document.kind.replaceAll('_',' ')}<details><summary>Document fingerprint</summary><code>{document.sha256}</code></details></li>)}</ul>:<p>No additional documents selected.</p>}
   {snapshot.approved?<Callout>This application snapshot is approved. Changes to its destination, CV, answers, sources, settings, or documents require another review.</Callout>:<>
    <label className="choice"><input type="checkbox" checked={confirmed} onChange={event=>setConfirmed(event.target.checked)}/>I approve this destination, attachments, and the displayed answers.</label>
    <button className="button primary" disabled={loading||!confirmed} onClick={()=>run(async()=>{
     setError('');setLoading(true);
     try{const approved=await api<ReviewSnapshot>('/jobs/'+job.id+'/review',json('POST',{token:snapshot.token,confirmation:true}));setSnapshot(approved);setConfirmed(false);await refresh()}
     catch(reason){setError(reason instanceof Error?reason.message:'Could not approve this application.');throw reason}
     finally{setLoading(false)}
    })}>Approve and queue</button>
   </>}
  </>}
  <button className="button" disabled={loading} onClick={()=>setReload(value=>value+1)}>Refresh review</button>
 </section>;
}
