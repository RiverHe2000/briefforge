export async function api<T>(path:string,options:RequestInit={}):Promise<T>{
  const headers=new Headers(options.headers);
  if(options.body && !(options.body instanceof FormData))headers.set('Content-Type','application/json');
  const response=await fetch(`/api${path}`,{...options,headers});
  if(!response.ok){
    let detail:unknown;try{const body=await response.json();detail=body.detail??body.error;}catch{detail=response.statusText;}
    if(Array.isArray(detail))detail=detail.map(item=>item.msg??JSON.stringify(item)).join('；');
    throw new Error(typeof detail==='string'?detail:`请求失败 (${response.status})`);
  }
  return response.status===204?undefined as T:await response.json();
}
export const post=<T>(path:string,body:unknown={},idempotent=false)=>api<T>(path,{method:'POST',body:JSON.stringify(body),headers:idempotent?{'Idempotency-Key':crypto.randomUUID()}:undefined});
