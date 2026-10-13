import {useEffect,useState} from 'react';
import {api,json} from './api';
import {Callout,Field,formatTime} from './components';
import type {Job,Run} from './types';

interface Connection {enabled:boolean;configured:boolean;host:string;username:string;folder:string;port:number;authorized_at:string;last_sync_at:string;automatic_polling:boolean}
interface SyncResult {scanned:number;imported:number;duplicates:number;skipped:number;read_only:boolean}

export function MailConnection({job,run,refresh}:{job:Job;run:Run;refresh:()=>Promise<void>}){
 const [connection,setConnection]=useState<Connection|null>(null),[host,setHost]=useState(''),[username,setUsername]=useState(''),[folder,setFolder]=useState('INBOX'),[secret,setSecret]=useState('');
 const [authorized,setAuthorized]=useState(false),[syncAuthorized,setSyncAuthorized]=useState(false),[message,setMessage]=useState(''),[error,setError]=useState('');
 const apply=(value:Connection)=>{setConnection(value);setHost(value.host);setUsername(value.username);setFolder(value.folder||'INBOX');setSecret('');setAuthorized(false);setError('')};
 useEffect(()=>{let active=true;setSyncAuthorized(false);setSecret('');setAuthorized(false);setMessage('');api<Connection>('/email/connection').then(value=>{if(active)apply(value)}).catch(cause=>{if(active)setError(cause instanceof Error?cause.message:'Mailbox connection could not load')});return()=>{active=false}},[job.id]);
 const save=()=>run(async()=>{
  const password=secret;setSecret('');
  apply(await api<Connection>('/email/connection',json('POST',{host,username,folder,port:993,secret:password,confirmation:authorized})));
  setMessage('Read-only mailbox connection saved. Sync runs only when you explicitly request it.');await refresh();
 });
 const disconnect=()=>run(async()=>{apply(await api<Connection>('/email/connection',json('DELETE',{confirmation:true})));setSyncAuthorized(false);setMessage('Mailbox disconnected.');await refresh()});
 const sync=()=>run(async()=>{
  const result=await api<SyncResult>('/jobs/'+encodeURIComponent(job.id)+'/email-sync',json('POST',{confirmation:syncAuthorized}));setSyncAuthorized(false);
  setMessage(`Checked ${result.scanned} recent message${result.scanned===1?'':'s'}; imported ${result.imported}, already saved ${result.duplicates}, skipped ${result.skipped}. Review any stage suggestions before accepting them.`);
  apply(await api<Connection>('/email/connection'));await refresh();
 });
 return <section className="drawer-section" aria-label="Optional read-only mailbox integration"><h3>Optional mailbox connection</h3><p>Connect an IMAP mailbox using an app-password. Pause the agent before saving, disconnecting, or syncing. Each sync reads at most 20 recent messages and imports only messages mentioning this employer and role or an explicit job reference.</p><p>Email remains unverified evidence. Sync never changes application status, sends mail, marks messages read, or deletes messages. There is no background polling.</p>
  {error&&<Callout>{error}</Callout>}
  <form onSubmit={event=>{event.preventDefault();if(authorized&&host&&username&&secret)save()}}>
   <Field label="Exact public IMAP hostname" help="Verified TLS on port 993 only; no scheme or path."><input required maxLength={253} value={host} onChange={event=>{setHost(event.target.value);setAuthorized(false)}} placeholder="imap.example.com" autoComplete="off" spellCheck={false}/></Field>
   <Field label="Mailbox email username"><input type="email" required maxLength={254} value={username} onChange={event=>{setUsername(event.target.value);setAuthorized(false)}} autoComplete="username"/></Field>
   <Field label="Folder" help="Simple ASCII folder names; default INBOX."><input required maxLength={200} value={folder} onChange={event=>{setFolder(event.target.value);setAuthorized(false)}} autoComplete="off"/></Field>
   <Field label="App-password" help="Stored only in the OS keychain and cleared from this form after saving."><input type="password" required maxLength={4096} value={secret} onChange={event=>{setSecret(event.target.value);setAuthorized(false)}} autoComplete="new-password"/></Field>
   <label className="choice"><input type="checkbox" checked={authorized} onChange={event=>setAuthorized(event.target.checked)}/>I authorize this connection for explicit, read-only email sync.</label>
   <button type="submit" className="button secondary" disabled={!authorized||!host||!username||!secret}>Save read-only connection</button>
  </form>
  {connection?.enabled&&<><p>Saved mailbox: {connection.username} on {connection.host}:993 · {connection.folder}</p><small>Last sync: {formatTime(connection.last_sync_at)}</small><label className="choice"><input type="checkbox" checked={syncAuthorized} onChange={event=>setSyncAuthorized(event.target.checked)}/>I authorize reading recent email for {job.company} — {job.title} now.</label><div className="card-actions"><button className="button secondary" disabled={!syncAuthorized} onClick={sync}>Sync employer email for this job</button><button className="button secondary" onClick={disconnect}>Disconnect mailbox</button></div></>}
  {message&&<p role="status">{message}</p>}
  <p className="help">Your provider must support IMAP login with an app-password. OAuth-only accounts are not supported by this connector.</p>
 </section>;
}
