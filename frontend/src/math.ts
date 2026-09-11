// Adapt LaTeX delimiters before CommonMark consumes their backslashes.
// This is a display adapter: stored transcripts are never rewritten.
export function normalizeMath(source:string):string {
  function inline(text:string){let out='',i=0;
    while(i<text.length){
      if(text[i]==='`'){const run=/^`+/.exec(text.slice(i))![0];let end=text.indexOf(run,i+run.length);while(end>=0&&(text[end-1]==='`'||text[end+run.length]==='`'))end=text.indexOf(run,end+run.length);if(end>=0){out+=text.slice(i,end+run.length);i=end+run.length;continue}}
      if(text[i]===']'&&text[i+1]==='('){let end=i+2,depth=1;for(;end<text.length&&depth;end++){if(text[end]==='\\'){end++;continue}if(text[end]==='(')depth++;if(text[end]===')')depth--}out+=text.slice(i,end);i=end;continue}
      if(text[i]==='$'){const delimiter=text[i+1]==='$'?'$$':'$';const end=text.indexOf(delimiter,i+delimiter.length);if(end>=0){out+=text.slice(i,end+delimiter.length);i=end+delimiter.length;continue}}
      if(text[i]==='\\'){
        if(text[i+1]==='\\'){out+=text.slice(i,i+2);i+=2;continue}
        const close=text[i+1]==='['?'\\]':text[i+1]==='('?'\\)':null;
        if(close){const end=text.indexOf(close,i+2);if(end<0){out+=text.slice(i);break}const body=text.slice(i+2,end);out+=close==='\\]'?'\n\n$$\n'+body.trim()+'\n$$\n\n':'$'+body.trim()+'$';i=end+2;continue}
        out+=text.slice(i,i+2);i+=2;continue;
      }
      out+=text[i++];
    }return out;
  }
  const parts:string[]=[];let prose='',fence='',width=0;
  function flush(){if(prose){parts.push(inline(prose));prose=''}}
  for(const line of source.match(/[^\n]*\n|[^\n]+$/g)||[]){
    const marker=/^ {0,3}(`{3,}|~{3,})/.exec(line);
    if(fence){parts.push(line);if(marker&&marker[1][0]===fence&&marker[1].length>=width&&/^\s*$/.test(line.slice(marker[0].length)))fence='';continue}
    if(marker){flush();fence=marker[1][0];width=marker[1].length;parts.push(line);continue}
    if(/^( {4}|\t)/.test(line)){flush();parts.push(line);continue}
    const bare=/^\s*\[([^\n]+)\]\s*$/.exec(line);
    if(bare&&/\\(?:mathrm|mathbf|mathbb|mathcal|frac|sqrt|sum|prod|int|sim|to|quad|Delta|alpha|beta|theta|lambda|left|right)\b/.test(bare[1]))prose+='\\['+bare[1]+'\\]\n';
    else prose+=line;
  }
  flush();return parts.join('');
}
