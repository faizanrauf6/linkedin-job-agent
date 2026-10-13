import {useEffect,useState} from 'react';
import {api} from './api';
import {Panel,Callout} from './components';
type Capability={name:string;status:string;adapter_version:string;supported_scopes:string[];limitations:string[];live_certified:boolean};
export function PortalSupport(){
 const [items,setItems]=useState<Capability[]>([]),[error,setError]=useState('');
 useEffect(()=>{api<Capability[]>('/portals/capabilities').then(setItems).catch(e=>setError(e.message))},[]);
 return <Panel title="Supported application journeys" subtitle="Fixture verification describes tested journeys. Live employer configurations can differ.">{error&&<Callout>{error}</Callout>}<div className="table-wrap"><table><thead><tr><th>Portal</th><th>Verification</th><th>Supported journey</th></tr></thead><tbody>{items.map(i=><tr key={i.name}><td>{i.name} · v{i.adapter_version}</td><td>{i.status.replaceAll('_',' ')}{i.live_certified?' · live certified':''}</td><td><details><summary>{i.supported_scopes.length} capabilities</summary><p>{i.supported_scopes.join(', ').replaceAll('_',' ')}</p><p>Needs intervention: {i.limitations.join(', ').replaceAll('_',' ')}</p></details></td></tr>)}</tbody></table></div></Panel>;
}
