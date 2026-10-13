import {useEffect,useState} from 'react';
import {api,json} from './api';
import {Callout,Field,ListInput,mapText,Panel,parseMap,SaveButton,Toggle,formatTime} from './components';
import type {Config,Run,Settings} from './types';

type Props={config:Config;save:(c:Config)=>Promise<void>;run:Run};
type Check={state:string;details:string;checked_at:string;submitted?:boolean};
type Validation={session:Check|null;preparation:Check|null;confirmed_submissions:number};
type Startup={supported:boolean;enabled:boolean;message:string;config_path?:string};
type Portal={host:string;suspended_until:string;reason:string};

export function AdvancedSettings({config,save,run}:Props){
 const [settings,setSettings]=useState<Settings>(structuredClone(config.settings));
 const [aliases,setAliases]=useState(mapText(settings.technology_aliases));
 const [sso,setSso]=useState(Object.entries(settings.sso_hosts).map(([host,allowed])=>`${host} = ${allowed.join(', ')}`).join('\n'));
 const patch=(key:keyof Settings,value:unknown)=>setSettings(s=>({...s,[key]:value}));
 return <><form onSubmit={e=>{e.preventDefault();run(async()=>{const hosts=parseMap(sso);await save({...config,settings:{...settings,paused:config.settings.paused,technology_aliases:parseMap(aliases),sso_hosts:Object.fromEntries(Object.entries(hosts).map(([host,allowed])=>[host,String(allowed).split(',').map(s=>s.trim()).filter(Boolean)]))}})})}}>
 <Panel title="Search coverage and recovery" subtitle="Control how far each search runs and how temporary failures are retried.">
 <div className="form-grid">
 <Field label="Maximum search pages"><input type="number" min="1" max="100" value={settings.max_search_pages} onChange={e=>patch('max_search_pages',Number(e.target.value))}/></Field>
 <Field label="Maximum attempts before attention"><input type="number" min="1" max="10" value={settings.retry_attempts} onChange={e=>patch('retry_attempts',Number(e.target.value))}/></Field>
 <Field label="Initial retry delay (seconds)"><input type="number" min="1" max="3600" value={settings.retry_backoff_seconds} onChange={e=>patch('retry_backoff_seconds',Number(e.target.value))}/></Field>
 <Field label="Portal cooldown (minutes)"><input type="number" min="1" max="1440" value={settings.portal_cooldown_minutes} onChange={e=>patch('portal_cooldown_minutes',Number(e.target.value))}/></Field>
 <Field label="Technology aliases" help="One Alias = Technology per line. Used for job and CV matching." wide><textarea rows={4} value={aliases} onChange={e=>setAliases(e.target.value)} placeholder="ReactJS = React"/></Field>
 </div><Callout>Searches stop at the page or job limit. A submission with an uncertain outcome always waits for your review.</Callout></Panel>
 <Panel title="Portal permissions" subtitle="Enable only the account and sign-in services you intend to use.">
 <Toggle label="Allow account creation" checked={settings.allow_registration} onChange={v=>patch('allow_registration',v)} help="Creates accounts only on the exact approved portals below. Email verification and personal agreements may still need you."/>
 <div className="form-grid"><Field label="Approved account creation portals" help="Exact hostnames, separated by commas. No scheme or path." wide><ListInput value={settings.registration_hosts} onChange={v=>patch('registration_hosts',v)} placeholder="careers.company.com"/></Field>
 <Field label="Additional sign-in and form hosts" help="One Portal hostname = Allowed hostname, Allowed hostname per line. Approve only services the employer actually uses." wide><textarea rows={4} value={sso} onChange={e=>setSso(e.target.value)} placeholder="careers.company.com = login.company.com"/></Field></div></Panel>
 <Panel title="Stay available"><Toggle label="Keep computer awake while the agent is running" checked={settings.prevent_sleep} onChange={v=>patch('prevent_sleep',v)} help="On macOS, prevents idle sleep while processing is enabled. It does not override closing the laptop or shutting down."/></Panel>
 <div className="form-footer"><span>Save portal and recovery preferences before testing.</span><SaveButton/></div></form>
 <OperationsControls config={config} run={run}/></>;
}

function OperationsControls({config,run}:{config:Config;run:Run}){
 const [validation,setValidation]=useState<Validation|null>(null),[startup,setStartup]=useState<Startup|null>(null),[portals,setPortals]=useState<Portal[]>([]),[error,setError]=useState('');
 const load=async()=>{const [v,s,p]=await Promise.all([api<Validation>('/validation'),api<Startup>('/startup'),api<Portal[]>('/portals')]);setValidation(v);setStartup(s);setPortals(p);setError('')};
 useEffect(()=>{load().catch(e=>setError(e.message))},[config.revision]);
 return <><Panel title="Live connection checks" subtitle="Check your account without sending an application.">
 <p className="help">Pause the agent, open LinkedIn, and sign in. Check the session here, then use Prepare without submitting on an opportunity to test its form.</p>
 {error&&<Callout>{error}</Callout>}
 <div className="form-grid"><div><strong>LinkedIn session</strong><p>{validation?.session?`${validation.session.state}: ${validation.session.details}`:'Not checked yet'}</p><small>{formatTime(validation?.session?.checked_at||'')}</small></div><div><strong>Application preparation</strong><p>{validation?.preparation?.details||'Not checked yet'}</p><small>{formatTime(validation?.preparation?.checked_at||'')}</small></div></div>
 <div className="card-actions"><button className="button secondary" onClick={()=>run(async()=>{await api('/browser/login',{method:'POST'})})}>Open LinkedIn</button><button className="button primary" disabled={!config.settings.paused} onClick={()=>run(async()=>{await api('/validation/session',{method:'POST'});await load()})}>Check signed-in session</button></div></Panel>
 <Panel title="Start with your computer" subtitle="Optional macOS login startup."><p className="help">{startup?.message||'Checking startup support…'}</p><div className="card-actions"><button className="button secondary" disabled={!startup?.supported} onClick={()=>run(async()=>{setStartup(await api<Startup>('/startup',json('POST',{enabled:!startup?.enabled})))})}>{startup?.enabled?'Disable login startup':'Enable login startup'}</button></div></Panel>
 <Panel title="Portals waiting to recover" subtitle="Other portals continue while these wait.">{portals.length?portals.map(p=><div className="form-grid" key={p.host}><div><strong>{p.host}</strong><p>{p.reason}</p><small>Resume after {formatTime(p.suspended_until)}</small></div><button className="button secondary" onClick={()=>run(async()=>{await api('/portals/'+encodeURIComponent(p.host)+'/resume',{method:'POST'});await load()})}>Resume this portal</button></div>):<p className="help">No suspended portals.</p>}</Panel></>;
}
