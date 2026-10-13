import {useState} from 'react';
import {api,json} from './api';
import {Field,Callout} from './components';
import type {Job,Run} from './types';

export function Intervention({job,run,refresh}:{job:Job;run:Run;refresh:()=>Promise<void>}){
 const [answers,setAnswers]=useState<Record<string,string>>({});
 const [confirmed,setConfirmed]=useState(false);
 const fields=job.field_details||[];
 return <div className="drawer-section"><h3>Continue after my input</h3><p>These questions and choices were recorded from the application. Confirm your answers to resume from a durable checkpoint. Browser recovery reinspects the form and CV.</p>{fields.map(f=><Field key={f.key} label={f.label} help={f.reason}>{f.personal_action||f.ambiguous||['file','password'].includes(f.type)?<Callout>Complete this step personally in the agent browser.</Callout>:f.options.length?<select value={answers[f.key]??''} onChange={e=>setAnswers({...answers,[f.key]:e.target.value})}><option value="">Choose your answer</option>{f.options.map(o=><option key={o}>{o}</option>)}</select>:<input maxLength={f.max_length??10000} value={answers[f.key]??''} onChange={e=>setAnswers({...answers,[f.key]:e.target.value})}/>}</Field>)}<label className="choice"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>I confirm these answers are accurate.</label><button className="button primary" disabled={!confirmed} onClick={()=>run(async()=>{await api('/jobs/'+job.id+'/continue',json('POST',{confirmation:true,answers:Object.fromEntries(Object.entries(answers).filter(([,v])=>v.trim()))}));await refresh()})}>Continue after my input</button></div>;
}
