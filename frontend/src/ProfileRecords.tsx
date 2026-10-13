import {type ReactNode} from 'react';
import {Plus, Trash2} from 'lucide-react';
import {Field, Panel} from './components';

interface DatedRecord {
 id: string; organization: string; start_date: string|null; end_date: string|null;
 current: boolean|null; description: string;
}
export interface EmploymentRecord extends DatedRecord {title: string}
export interface EducationRecord extends DatedRecord {degree: string; field_of_study: string}
export interface CredentialRecord {
 id: string; name: string; issuer: string; identifier: string;
 issue_date: string|null; expiry_date: string|null;
}
export interface LanguageRecord {
 id: string; name: string; proficiency: 'basic'|'conversational'|'professional'|'fluent'|'native'|null;
}
export interface ApplicantRecords {
 employment: EmploymentRecord[];
 education: EducationRecord[];
 certifications: CredentialRecord[];
 licenses: CredentialRecord[];
 languages: LanguageRecord[];
 address: {line1:string;line2:string;city:string;region:string;postal_code:string;country:string};
 preferences: {willing_to_travel:boolean|null;travel_percent:number|null;shifts:('day'|'evening'|'night'|'rotating')[];willing_to_work_weekends:boolean|null;willing_to_work_overtime:boolean|null};
 salary: {amount:number|null;currency:string|null;period:'year'|'month'|'week'|'day'|'hour'|null};
 availability: {start_date:string|null;notice_days:number|null;immediate:boolean|null};
}

export function emptyApplicantRecords(): ApplicantRecords {
 return {
  employment:[],education:[],certifications:[],licenses:[],languages:[],
  address:{line1:'',line2:'',city:'',region:'',postal_code:'',country:''},
  preferences:{willing_to_travel:null,travel_percent:null,shifts:[],willing_to_work_weekends:null,willing_to_work_overtime:null},
  salary:{amount:null,currency:null,period:null},availability:{start_date:null,notice_days:null,immediate:null},
 };
}

type Section = 'employment'|'education'|'certifications'|'licenses'|'languages';
type AnyRecord = EmploymentRecord|EducationRecord|CredentialRecord|LanguageRecord;
const templates = {
 employment:()=>({id:crypto.randomUUID(),organization:'',title:'',start_date:null,end_date:null,current:null,description:''} as EmploymentRecord),
 education:()=>({id:crypto.randomUUID(),organization:'',degree:'',field_of_study:'',start_date:null,end_date:null,current:null,description:''} as EducationRecord),
 certifications:()=>({id:crypto.randomUUID(),name:'',issuer:'',identifier:'',issue_date:null,expiry_date:null} as CredentialRecord),
 licenses:()=>({id:crypto.randomUUID(),name:'',issuer:'',identifier:'',issue_date:null,expiry_date:null} as CredentialRecord),
 languages:()=>({id:crypto.randomUUID(),name:'',proficiency:null} as LanguageRecord),
};

export function ProfileRecords({value,onChange}:{value?:Partial<ApplicantRecords>|null;onChange:(value:ApplicantRecords)=>void}) {
 const defaults=emptyApplicantRecords();
 const records:ApplicantRecords={...defaults,...value,
  employment:value?.employment??[],education:value?.education??[],certifications:value?.certifications??[],licenses:value?.licenses??[],languages:value?.languages??[],
  address:{...defaults.address,...value?.address},preferences:{...defaults.preferences,...value?.preferences},salary:{...defaults.salary,...value?.salary},availability:{...defaults.availability,...value?.availability},
 };
 const patch=<K extends keyof ApplicantRecords>(section:K,change:Partial<ApplicantRecords[K]>)=>onChange({...records,[section]:{...records[section],...change}});
 const edit=(section:Section,id:string,change:Record<string,unknown>)=>onChange({...records,[section]:records[section].map(item=>item.id===id?{...item,...change}:item)});
 const triState=(label:string,current:boolean|null,onSelect:(next:boolean|null)=>void)=><Field label={label}><select value={current===null?'':String(current)} onChange={e=>onSelect(e.target.value===''?null:e.target.value==='true')}><option value="">Not specified</option><option value="true">Yes</option><option value="false">No</option></select></Field>;
 const sections:[Section,string,string][]=[
  ['employment','Employment history','employment record'],['education','Education history','education record'],
  ['certifications','Certifications','certification'],['licenses','Professional licenses','license'],['languages','Languages','language'],
 ];
 const editor=(section:Section,item:AnyRecord):ReactNode=>{
  const input=(key:string,label:string,type='text',extra:Record<string,unknown>={})=><Field label={label}><input type={type} value={String((item as unknown as Record<string,unknown>)[key]??'')} {...extra} onChange={e=>edit(section,item.id,{[key]:e.target.value||(type==='date'?null:'')})}/></Field>;
  if(section==='employment'||section==='education') {
   const record=item as EmploymentRecord|EducationRecord;
   return <>
    {input('organization',section==='employment'?'Employer name':'Institution name')}
    {section==='employment'?input('title','Job title'):<>{input('degree','Degree or qualification')}{input('field_of_study','Field of study')}</>}
    {input('start_date','Start date','date',{max:record.end_date??undefined})}
    {input('end_date',section==='education'?'Graduation or end date':'End date','date',{min:record.start_date??undefined,disabled:record.current===true})}
    {triState(section==='employment'?'I currently work here':'I currently study here',record.current??null,current=>edit(section,item.id,{current,...(current===true?{end_date:null}:{})}))}
    <Field label={section==='employment'?'Responsibilities and achievements':'Education description'} wide><textarea rows={3} value={record.description??''} onChange={e=>edit(section,item.id,{description:e.target.value})}/></Field>
   </>;
  }
  if(section==='languages') {
   const record=item as LanguageRecord;
   return <>{input('name','Language')}<Field label="Language proficiency"><select value={record.proficiency??''} onChange={e=>edit(section,item.id,{proficiency:e.target.value||null})}><option value="">Not specified</option><option value="basic">Basic</option><option value="conversational">Conversational</option><option value="professional">Professional</option><option value="fluent">Fluent</option><option value="native">Native</option></select></Field></>;
  }
  const record=item as CredentialRecord;
  return <>{input('name',section==='licenses'?'License name':'Certification name')}{input('issuer','Issuing organization')}{input('identifier',section==='licenses'?'License number':'Certification identifier')}{input('issue_date','Issue date','date',{max:record.expiry_date??undefined})}{input('expiry_date','Expiry date','date',{min:record.issue_date??undefined})}</>;
 };
 return <>
  {sections.map(([section,title,singular])=><Panel key={section} title={title} subtitle="Enter confirmed facts. Leave unknown information blank." action={<button type="button" className="button secondary" onClick={()=>onChange({...records,[section]:[...records[section],templates[section]()]})}><Plus size={16}/>Add {singular}</button>}>
   {records[section].length===0?<p className="help">No {title.toLowerCase()} saved.</p>:records[section].map((item,index)=><fieldset key={item.id}><legend>{title} {index+1}</legend><div className="form-grid">{editor(section,item)}</div><button type="button" className="button secondary" aria-label={`Remove ${singular} ${index+1}`} onClick={()=>onChange({...records,[section]:records[section].filter(record=>record.id!==item.id)})}><Trash2 size={16}/>Remove {singular}</button></fieldset>)}
  </Panel>)}
  <Panel title="Mailing address"><div className="form-grid">{([
   ['line1','Street address'],['line2','Address line 2'],['city','City'],['region','State or province'],['postal_code','Postal code'],['country','Address country'],
  ] as const).map(([key,label])=><Field key={key} label={label}><input value={records.address[key]} onChange={e=>patch('address',{[key]:e.target.value})}/></Field>)}</div></Panel>
  <Panel title="Travel and shift preferences"><div className="form-grid">
   {triState('Are you willing to travel?',records.preferences.willing_to_travel,willing_to_travel=>patch('preferences',{willing_to_travel,...(willing_to_travel===false?{travel_percent:null}:{})}))}
   <Field label="Maximum travel percentage"><input type="number" min="0" max="100" step="1" disabled={records.preferences.willing_to_travel===false} value={records.preferences.travel_percent??''} onChange={e=>patch('preferences',{travel_percent:e.target.value===''?null:Number(e.target.value)})}/></Field>
   <fieldset className="field wide"><legend>Preferred shifts</legend><small>Select only shifts you accept.</small><div className="choice-row">{(['day','evening','night','rotating'] as const).map(shift=><label key={shift} className={'choice '+(records.preferences.shifts.includes(shift)?'selected':'')}><input type="checkbox" checked={records.preferences.shifts.includes(shift)} onChange={e=>patch('preferences',{shifts:e.target.checked?[...records.preferences.shifts,shift]:records.preferences.shifts.filter(s=>s!==shift)})}/>{shift.charAt(0).toUpperCase()+shift.slice(1)}</label>)}</div></fieldset>
   {triState('Are you willing to work weekends?',records.preferences.willing_to_work_weekends,willing_to_work_weekends=>patch('preferences',{willing_to_work_weekends}))}
   {triState('Are you willing to work overtime?',records.preferences.willing_to_work_overtime,willing_to_work_overtime=>patch('preferences',{willing_to_work_overtime}))}
  </div></Panel>
  <Panel title="Salary expectation" subtitle="Specify the amount, currency, and period together. No conversion is assumed."><div className="form-grid">
   <Field label="Salary amount"><input type="number" min="0" step="any" value={records.salary.amount??''} onChange={e=>patch('salary',{amount:e.target.value===''?null:Number(e.target.value)})}/></Field>
   <Field label="Salary currency" help="Three-letter currency code, for example PKR or USD."><input pattern="[A-Z]{3}" maxLength={3} required={records.salary.amount!==null} value={records.salary.currency??''} onChange={e=>patch('salary',{currency:e.target.value.toUpperCase()||null})}/></Field>
   <Field label="Salary period"><select required={records.salary.amount!==null} value={records.salary.period??''} onChange={e=>patch('salary',{period:(e.target.value||null) as ApplicantRecords['salary']['period']})}><option value="">Not specified</option>{(['year','month','week','day','hour'] as const).map(period=><option key={period} value={period}>Per {period}</option>)}</select></Field>
  </div></Panel>
  <Panel title="Availability"><div className="form-grid">
   {triState('Are you available immediately?',records.availability.immediate,immediate=>patch('availability',{immediate,...(immediate===true?{start_date:null,notice_days:null}:{})}))}
   <Field label="Available start date"><input type="date" disabled={records.availability.immediate===true} value={records.availability.start_date??''} onChange={e=>patch('availability',{start_date:e.target.value||null})}/></Field>
   <Field label="Notice period in days"><input type="number" min="0" max="3650" step="1" disabled={records.availability.immediate===true} value={records.availability.notice_days??''} onChange={e=>patch('availability',{notice_days:e.target.value===''?null:Number(e.target.value)})}/></Field>
  </div></Panel>
 </>;
}
