import {useEffect,useRef,useState} from 'react';
import {FileText,Trash2,UploadCloud} from 'lucide-react';
import {api,json} from './api';
import {Callout,Field,Panel} from './components';
import type {Run} from './types';

export type DocumentKind='cover_letter'|'certificate'|'license'|'portfolio'|'other';
export interface SupportingDocument {
 id:string;label:string;kind:DocumentKind;filename:string;sha256:string;confirmed:boolean;created_at:string;
}
const kinds:{value:DocumentKind;label:string}[]=[
 {value:'cover_letter',label:'Cover letter'},{value:'certificate',label:'Certificate'},
 {value:'license',label:'License'},{value:'portfolio',label:'Portfolio'},{value:'other',label:'Other supporting document'},
];

export function Documents({run}:{run:Run}) {
 const [documents,setDocuments]=useState<SupportingDocument[]>([]);
 const [label,setLabel]=useState(''),[kind,setKind]=useState<DocumentKind>('other');
 const [file,setFile]=useState<File|null>(null),[working,setWorking]=useState(false),[error,setError]=useState('');
 const fileInput=useRef<HTMLInputElement>(null);
 const refresh=async()=>setDocuments(await api<SupportingDocument[]>('/documents'));
 useEffect(()=>{
  let mounted=true;
  api<SupportingDocument[]>('/documents').then(value=>{if(mounted)setDocuments(value)}).catch((failure:Error)=>{if(mounted)setError(failure.message)});
  return()=>{mounted=false};
 },[]);
 const perform=(work:()=>Promise<void>)=>{
  setWorking(true);setError('');
  run(async()=>{try{await work();await refresh()}finally{setWorking(false)}});
 };
 const upload=()=>{
  if(!file){setError('Choose a document to upload.');return}
  if(file.size>10*1024*1024){setError('Choose a document up to 10 MB.');return}
  perform(async()=>{
   const form=new FormData();form.append('file',file);form.append('label',label);form.append('kind',kind);
   await api('/documents',{method:'POST',body:form});
   setFile(null);setLabel('');if(fileInput.current)fileInput.current.value='';
  });
 };
 return <>
  <Panel title="Supporting documents" subtitle="Keep cover letters, certificates, licenses, and portfolios alongside your CVs.">
   <Callout>Pause the agent before changing documents. Review each original, then confirm it for applications. Give documents distinct labels when you have more than one of the same type.</Callout>
   <div className="form-grid">
    <Field label="Supporting document file" help="PDF, DOCX, or UTF-8 TXT · Up to 10 MB · Scanned PDFs are accepted."><input ref={fileInput} type="file" accept=".pdf,.docx,.txt" disabled={working} onChange={event=>setFile(event.target.files?.[0]??null)}/></Field>
    <Field label="Document label" help="Use the application field name when this document has a specific purpose."><input maxLength={200} value={label} disabled={working} onChange={event=>setLabel(event.target.value)} placeholder="For example, nursing license"/></Field>
    <Field label="Document type"><select value={kind} disabled={working} onChange={event=>setKind(event.target.value as DocumentKind)}>{kinds.map(item=><option key={item.value} value={item.value}>{item.label}</option>)}</select></Field>
   </div>
   <button type="button" className="button secondary" disabled={working||!file} onClick={upload}><UploadCloud size={16}/>Upload supporting document</button>
   {error&&<p role="alert">{error}</p>}
  </Panel>
  {documents.length===0?<p className="help">No supporting documents saved.</p>:<div className="cv-grid">{documents.map(record=><Panel key={record.id} className="cv-card">
   <div className="search-card-top"><div className="file-icon"><FileText size={24}/></div><span className={'pill '+(record.confirmed?'green':'amber')}>{record.confirmed?'Ready to use':'Review needed'}</span></div>
   <h3>{record.label}</h3><p>{kinds.find(item=>item.value===record.kind)?.label}</p><p className="file-name">{record.filename}</p>
   <a className="text-link" href={'/api/documents/'+encodeURIComponent(record.id)+'/file'} download={record.filename}>Download original {record.label}</a>
   <label className="check-line"><input type="checkbox" checked={record.confirmed} disabled={working} onChange={event=>{const confirmed=event.target.checked;perform(async()=>{await api('/documents/'+encodeURIComponent(record.id),json('PATCH',{confirmed}))})}}/>I reviewed {record.label} and approve using this original for applications.</label>
   <button type="button" className="button secondary" disabled={working} aria-label={'Delete supporting document '+record.label} onClick={()=>{if(confirm('Delete '+record.label+' and its saved original?'))perform(async()=>{await api('/documents/'+encodeURIComponent(record.id),{method:'DELETE'})})}}><Trash2 size={16}/>Delete document</button>
  </Panel>)}</div>}
 </>;
}

export default Documents;
