import {useLayoutEffect,useState} from 'react';
import {Monitor,Moon,Sun} from 'lucide-react';

type Theme='light'|'dark'|'system';
const STORAGE_KEY='orbit.theme';

function savedTheme():Theme{
 try{
  const value=localStorage.getItem(STORAGE_KEY);
  if(value==='light'||value==='dark')return value;
 }catch{/* Theme selection still works when browser storage is unavailable. */}
 return 'system';
}

function applyTheme(theme:Theme,systemDark:boolean){
 const resolved=theme==='system'?(systemDark?'dark':'light'):theme;
 document.documentElement.dataset.theme=resolved;
 document.documentElement.style.colorScheme=resolved;
 document.querySelector('meta[name="theme-color"]')?.setAttribute('content',resolved==='dark'?'#111713':'#f7f8f5');
}

export function ThemeControl(){
 const [theme,setTheme]=useState<Theme>(savedTheme);
 useLayoutEffect(()=>{
  const media=window.matchMedia('(prefers-color-scheme: dark)');
  const update=()=>applyTheme(theme,media.matches);
  const sync=(event:StorageEvent)=>{if(event.key===STORAGE_KEY||event.key===null)setTheme(savedTheme())};
  update();
  media.addEventListener('change',update);
  window.addEventListener('storage',sync);
  return()=>{media.removeEventListener('change',update);window.removeEventListener('storage',sync)};
 },[theme]);
 const options=[{value:'light',label:'Light',icon:Sun},{value:'dark',label:'Dark',icon:Moon},{value:'system',label:'System',icon:Monitor}] as const;
 return <div className="theme-control" role="group" aria-label="Color theme">{options.map(option=><button key={option.value} type="button" aria-label={option.label+' theme'} title={option.label+' theme'} aria-pressed={theme===option.value} onClick={()=>{
  setTheme(option.value);
  try{localStorage.setItem(STORAGE_KEY,option.value)}catch{/* Keep the current selection in memory. */}
 }}><option.icon size={15} aria-hidden="true"/></button>)}</div>;
}
