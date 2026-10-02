import type {ResearchEvent,Source} from './types';
export const activeRun=(status?:string)=>status==='running'||status==='queued';
export const money=(n:number|undefined)=>`$${(n??0).toFixed(4)}`;
const validDate=(v?:string|null)=>!!v&&!Number.isNaN(new Date(v).getTime());
export const date=(v?:string|null)=>validDate(v)?new Intl.DateTimeFormat('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}).format(new Date(v!)):'未披露';
export const day=(v?:string|null)=>validDate(v)?new Intl.DateTimeFormat('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(v!)):'日期未披露';
export function safeSourceUrl(url?:string|null):string|undefined{try{const parsed=new URL(url??'');return ['http:','https:'].includes(parsed.protocol)?parsed.href:undefined}catch{return undefined}}
export const statusLabel=(status:string)=>({queued:'等待启动',pending:'待执行',running:'研究中',completed:'已完成',failed:'执行失败',cancelled:'已取消',budget_exceeded:'达到预算上限',supported:'有证据支持',uncertain:'无法确认',contradicted:'证据存在冲突',ready:'待开始',draft:'研究草稿',processing:'处理中'}[status]??status);
export const mergeEvents=(previous:ResearchEvent[],incoming:ResearchEvent[])=>Array.from(new Map([...previous,...incoming].map(e=>[`${e.run_id}:${e.id}`,e])).values()).sort((a,b)=>a.id-b.id);
export function quoteParts(text:string,quote:string):{before:string;match:string;after:string}{const index=quote?text.indexOf(quote):-1;return index<0?{before:text,match:'',after:''}:{before:text.slice(0,index),match:quote,after:text.slice(index+quote.length)};}
export const latestSources=(sources:Source[])=>sources.filter(s=>s.active);
export const splitList=(text:string)=>[...new Set(text.split(/[,，\n、；;]/).map(s=>s.trim()).filter(Boolean))];
