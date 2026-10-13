export async function api<T>(path:string, init:RequestInit={}):Promise<T> {
  const headers=new Headers(init.headers);
  if(init.body && !(init.body instanceof FormData)) headers.set('Content-Type','application/json');
  const response=await fetch('/api'+path,{...init,headers});
  const data=await response.json();
  if(!response.ok) {
    const detail=data.detail;
    throw new Error(typeof detail==='string'?detail:Array.isArray(detail)?detail.map((e:{msg:string})=>e.msg).join('; '):'Request failed');
  }
  return data as T;
}
export function json(method:string,body:unknown):RequestInit{return {method,body:JSON.stringify(body)}}
