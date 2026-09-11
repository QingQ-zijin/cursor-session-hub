"""Display-only delimiter compatibility, equivalent to frontend/src/math.ts."""
import re

def normalize_math(source):
    def inline(text):
        out=[];i=0
        while i<len(text):
            if text[i]=='`':
                run=re.match(r'`+',text[i:])[0];end=text.find(run,i+len(run))
                while end>=0 and ((end and text[end-1]=='`') or text[end+len(run):end+len(run)+1]=='`'):end=text.find(run,end+len(run))
                if end>=0:out.append(text[i:end+len(run)]);i=end+len(run);continue
            if text[i:i+2]=='](':
                end=i+2;depth=1
                while end<len(text) and depth:
                    if text[end]=='\\':end+=2;continue
                    if text[end]=='(':depth+=1
                    if text[end]==')':depth-=1
                    end+=1
                out.append(text[i:end]);i=end;continue
            if text[i]=='$':
                delimiter='$$' if text[i:i+2]=='$$' else '$';end=text.find(delimiter,i+len(delimiter))
                if end>=0:out.append(text[i:end+len(delimiter)]);i=end+len(delimiter);continue
            if text[i]=='\\':
                if text[i:i+2]=='\\\\':out.append(text[i:i+2]);i+=2;continue
                close={'[':'\\]', '(':'\\)'}.get(text[i+1:i+2])
                if close:
                    end=text.find(close,i+2)
                    if end<0:out.append(text[i:]);break
                    body=text[i+2:end].strip();out.append('\n\n$$\n'+body+'\n$$\n\n' if close=='\\]' else '$'+body+'$');i=end+2;continue
                out.append(text[i:i+2]);i+=2;continue
            out.append(text[i]);i+=1
        return ''.join(out)
    parts=[];prose=[];fence='';width=0
    def flush():
        if prose:parts.append(inline(''.join(prose)));prose.clear()
    for line in source.splitlines(keepends=True):
        marker=re.match(r'^ {0,3}(`{3,}|~{3,})',line)
        if fence:
            parts.append(line)
            if marker and marker[1][0]==fence and len(marker[1])>=width and not line[marker.end():].strip():fence=''
            continue
        if marker:flush();fence=marker[1][0];width=len(marker[1]);parts.append(line);continue
        if re.match(r'^( {4}|\t)',line):flush();parts.append(line);continue
        bare=re.match(r'^\s*\[([^\n]+)\]\s*$',line)
        if bare and re.search(r'\\(?:mathrm|mathbf|mathbb|mathcal|frac|sqrt|sum|prod|int|sim|to|quad|Delta|alpha|beta|theta|lambda|left|right)\b',bare[1]):prose.append('\\['+bare[1]+'\\]\n')
        else:prose.append(line)
    flush();return ''.join(parts)
