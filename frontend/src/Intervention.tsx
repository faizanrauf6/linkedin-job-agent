import {useState} from 'react';
import {api,json} from './api';
import {Field,Callout} from './components';
import type {Job,Run} from './types';

export function Intervention({job,run,refresh}:{job:Job;run:Run;refresh:()=>Promise<void>}){
 const [answers,setAnswers]=useState<Record<string,string>>({});
 const [confirmed,setConfirmed]=useState(false);
 const fields=job.field_details||[];
 return <div className="drawer-section"><h3>Continue after my input</h3><p>These questions and choices were recorded from the application. Confirm your answers to resume from a durable checkpoint. Browser recovery reinspects the form and CV.</p>{fields.map(f=><Field key={f.key} label={f.label} help={f.reason}>{f.personal_action||f.ambiguous||['file','password'].includes(f.type)?<Callout>Complete this step personally in the agent browser.</Callout>:<AnswerInput field={f} value={answers[f.key]??''} onChange={value=>setAnswers({...answers,[f.key]:value})}/>}</Field>)}<label className="choice"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>I confirm these answers are accurate.</label><button className="button primary" disabled={!confirmed} onClick={()=>run(async()=>{await api('/jobs/'+job.id+'/continue',json('POST',{confirmation:true,answers:Object.fromEntries(Object.entries(answers).filter(([,v])=>v.trim()))}));await refresh()})}>Continue after my input</button></div>;
}

function AnswerInput({field,value,onChange}:{field:Job['field_details'][number];value:string;onChange:(value:string)=>void}){
 if(field.options.length){
  if(['select-multiple','listbox'].includes(field.type)){
   let selected:string[]=[];
   try{const parsed=JSON.parse(value);if(Array.isArray(parsed))selected=parsed}catch{if(value)selected=[value]}
   return <select multiple value={selected} onChange={e=>onChange(JSON.stringify(Array.from(e.target.selectedOptions).map(o=>o.value)))}>{field.options.map(o=><option key={o}>{o}</option>)}</select>;
  }
  return <select value={value} onChange={e=>onChange(e.target.value)}><option value="">Choose your answer</option>{field.options.map(o=><option key={o}>{o}</option>)}</select>;
 }
 return <input maxLength={field.max_length??10000} value={value} onChange={e=>onChange(e.target.value)}/>;
}
