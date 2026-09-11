export function unnamed(title?:string){const value=(title||'').trim();return !value||['未命名 cursor 会话','未命名会话','未命名记录','untitled','new chat','new conversation','无法读取的 cursor 会话'].includes(value.toLowerCase())||/^[0-9a-f-]{32,36}$/i.test(value)}
export function batchDestination(){return localStorage.getItem('csh-batch-destination')==='server'?'server':'local'}
