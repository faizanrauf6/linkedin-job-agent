import {useState,type ReactNode} from 'react';
import {ArrowUpRight,Check,ChevronRight,Info,LoaderCircle} from 'lucide-react';

export function Field({label,help,children,wide=false}:{label:string;help?:string;children:ReactNode;wide?:boolean}){
 return <label className={'field '+(wide?'wide':'')}><span>{label}</span>{children}{help&&<small>{help}</small>}</label>;
}
export function ListInput({value,onChange,placeholder}:{value:string[];onChange:(v:string[])=>void;placeholder?:string}){
 const [text,setText]=useState(value.join(', '));
 return <input value={text} placeholder={placeholder} onChange={e=>{setText(e.target.value);onChange(e.target.value.split(',').map(s=>s.trim()).filter(Boolean))}}/>;
}
export function Toggle({checked,onChange,label,help}:{checked:boolean;onChange:(v:boolean)=>void;label:string;help?:string}){
 return <label className="toggle-row"><div><strong>{label}</strong>{help&&<small>{help}</small>}</div><input className="toggle" type="checkbox" checked={checked} onChange={e=>onChange(e.target.checked)}/></label>;
}
export function Empty({icon,title,description,action,onClick}:{icon:ReactNode;title:string;description:string;action?:string;onClick?:()=>void}){
 return <div className="empty"><div className="empty-icon">{icon}</div><h3>{title}</h3><p>{description}</p>{action&&<button className="button secondary" onClick={onClick}>{action}<ChevronRight size={15}/></button>}</div>;
}
export function Panel({title,subtitle,action,children,className=''}:{title?:string;subtitle?:string;action?:ReactNode;children:ReactNode;className?:string}){
 return <section className={'panel '+className}>{title&&<div className="panel-heading"><div><h2>{title}</h2>{subtitle&&<p>{subtitle}</p>}</div>{action}</div>}{children}</section>;
}
export function Badge({status}:{status:string}){
 const labels:Record<string,string>={awaiting_review:'Ready to review',needs_input:'Needs attention',submission_uncertain:'Check outcome',applying:'Applying',queued:'Queued',submitted:'Submitted',rejected:'Skipped',blocked:'Blocked',discovered:'Discovered'};
 return <span className={'badge '+status}><span/>{labels[status]||status}</span>;
}
export function Callout({children}:{children:ReactNode}){return <div className="callout"><Info size={17}/><div>{children}</div></div>}
export function SaveButton({busy=false}:{busy?:boolean}){return <button type="submit" className="button primary" disabled={busy}>{busy?<LoaderCircle size={16} className="spin"/>:<Check size={16}/>}Save changes</button>}
export function External({url,children}:{url:string;children:ReactNode}){
 const safe=/^https?:\/\//i.test(url);
 return safe?<a className="text-link" href={url} target="_blank" rel="noreferrer">{children}<ArrowUpRight size={14}/></a>:null;
}
export function formatTime(value:string){return value?new Date(value).toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}):'Not yet'}
export function mapText(value:Record<string,unknown>){return Object.entries(value).map(([k,v])=>`${k} = ${v===true?'Yes':v===false?'No':v}`).join('\n')}
export function parseMap(text:string,type:'string'|'number'|'boolean'='string'):Record<string,any>{
 const result:Record<string,any>={};
 for(const line of text.split('\n').filter(l=>l.trim())){
  const index=line.indexOf('=');if(index<1)throw new Error('Use one Key = Value entry per line.');
  const key=line.slice(0,index).trim(),value=line.slice(index+1).trim();
  if(!value)throw new Error(`Enter a value for ${key}.`);
  if(type==='number'){const num=Number(value);if(!Number.isFinite(num)||num<0)throw new Error(`Invalid experience for ${key}.`);result[key]=num}
  else if(type==='boolean'){if(!/^(yes|no|true|false)$/i.test(value))throw new Error(`Use Yes or No for ${key}.`);result[key]=/^(yes|true)$/i.test(value)}
  else result[key]=value;
 }
 return result;
}
