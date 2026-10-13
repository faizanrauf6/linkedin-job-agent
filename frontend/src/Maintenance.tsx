import {useEffect,useState} from 'react';
import {api} from './api';
import {Callout,Field,Panel,formatTime} from './components';
import type {Config,Run} from './types';

interface AdapterMetric {adapter:string;version:string;attempts:number;submitted:number;needs_input:number;failed:number;submission_uncertain:number;blocked:number;completion_ratio:number;errors:Record<string,number>;updated_at:string}
interface Metrics {adapters:AdapterMetric[];total_attempts:number;total_submitted:number;completion_ratio:number}

async function downloadBackup(){
 const response=await fetch('/api/maintenance/backup');
 if(!response.ok){const value=await response.json().catch(()=>({detail:'Backup could not be downloaded'}));throw new Error(typeof value.detail==='string'?value.detail:'Backup could not be downloaded')}
 const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement('a');
 link.href=url;link.download='agent-backup.zip';document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
}

export function Maintenance({config,run}:{config:Config;run:Run}){
 const [metrics,setMetrics]=useState<Metrics|null>(null),[error,setError]=useState('');
 const [file,setFile]=useState<File|null>(null),[confirmation,setConfirmation]=useState(''),[message,setMessage]=useState(''),[fileKey,setFileKey]=useState(0);
 const load=async()=>{setMetrics(await api<Metrics>('/maintenance/adapters'));setError('')};
 useEffect(()=>{let active=true;api<Metrics>('/maintenance/adapters').then(value=>{if(active){setMetrics(value);setError('')}}).catch(cause=>{if(active)setError(cause instanceof Error?cause.message:'Adapter measurements could not load')});return()=>{active=false}},[config.revision]);
 const restore=()=>{
  if(!file||confirmation!=='RESTORE BACKUP')return;
  const body=new FormData();body.set('file',file);body.set('confirmation',confirmation);
  run(async()=>{await api('/maintenance/restore',{method:'POST',body});setFile(null);setConfirmation('');setFileKey(value=>value+1);setMessage('Backup restored. The workspace is paused, and pending applications require review before continuing.');await load()});
 };
 return <>
  <Panel title="Backup and restore" subtitle="Keep a private copy of your local workspace.">
   <p className="help">Archives contain your profile, CVs, supporting documents, saved answers, application history, and checkpoints. Browser cookies and OS keychain credentials are excluded. Pause the agent and wait for current work to finish before exporting or restoring.</p>
   {!config.settings.paused&&<Callout>Pause the agent before backup or restore.</Callout>}
   <button className="button secondary" disabled={!config.settings.paused} onClick={()=>run(downloadBackup)}>Download private backup</button>
   <h3>Restore a backup</h3><p className="help">Restoring replaces the saved database and referenced documents. Applications stay paused; interrupted or uncertain submissions need reconciliation, and queued applications remain held for review.</p>
   <Field label="Backup ZIP file (up to 256 MB)"><input key={fileKey} type="file" accept=".zip,application/zip" onChange={event=>{setFile(event.target.files?.[0]||null);setConfirmation('');setMessage('')}}/></Field>
   <Field label="Type RESTORE BACKUP to confirm"><input value={confirmation} onChange={event=>setConfirmation(event.target.value)} placeholder="RESTORE BACKUP" autoComplete="off" spellCheck={false}/></Field>
   {file&&file.size>256*1024*1024&&<Callout>Choose a backup no larger than 256 MB.</Callout>}
   <button className="button danger-button" disabled={!config.settings.paused||!file||file.size>256*1024*1024||confirmation!=='RESTORE BACKUP'} onClick={restore}>Restore backup and keep paused</button>
   {message&&<p role="status">{message}</p>}
  </Panel>
  <Panel title="Measured adapter results" subtitle="Observed attempts in this workspace, grouped by adapter and version.">
   {error&&<Callout>{error}</Callout>}
   {metrics&&metrics.total_attempts>0?<>
    <p>{metrics.total_submitted} confirmed submission{metrics.total_submitted===1?'':'s'} across {metrics.total_attempts} attempt{metrics.total_attempts===1?'':'s'} · {(metrics.completion_ratio*100).toFixed(1)}% confirmed completion.</p>
    <div className="table-wrap"><table><thead><tr><th>Adapter</th><th>Version</th><th>Attempts</th><th>Confirmed</th><th>Completion</th><th>Needs input</th><th>Uncertain</th><th>Failed / blocked</th></tr></thead><tbody>{metrics.adapters.map(adapter=><tr key={adapter.adapter+':'+adapter.version}><td>{adapter.adapter}</td><td>{adapter.version}</td><td>{adapter.attempts}</td><td>{adapter.submitted}</td><td>{(adapter.completion_ratio*100).toFixed(1)}%</td><td>{adapter.needs_input}</td><td>{adapter.submission_uncertain}</td><td>{adapter.failed+adapter.blocked}</td></tr>)}</tbody></table></div>
    {metrics.adapters.map(adapter=><details key={adapter.adapter+':'+adapter.version}><summary>{adapter.adapter} {adapter.version} · error classes</summary><p>{Object.entries(adapter.errors).map(([name,count])=>name+': '+count).join(', ')||'No error classes recorded.'}</p><small>Last result {formatTime(adapter.updated_at)}</small></details>)}
   </>:<p className="help">No measured application attempts yet. A rate appears after attempts are recorded; it does not predict whether a future application will succeed.</p>}
   <button className="button secondary" onClick={()=>run(load)}>Refresh measurements</button>
  </Panel>
 </>;
}
