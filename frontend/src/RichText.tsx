import {Children,isValidElement,useRef,useState} from 'react';
import type {ReactNode} from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import {Check,Copy,Image as ImageIcon} from 'lucide-react';
import {copyText,safeLink} from './utils';
function CodeFrame({children}: {children: ReactNode}) {
  const pre = useRef<HTMLPreElement>(null);
  const [copied,setCopied] = useState(false), [error,setError] = useState(false);
  const child = Children.toArray(children)[0];
  const language = isValidElement<{className?: string}>(child) ? (child.props.className || '').replace('language-', '') : '';
  return <div className="code-frame"><div className="code-frame-header"><span>{language || 'code'}</span><button aria-label="复制代码" onClick={()=>void copyText(pre.current?.textContent || '').then(()=>{setCopied(true);setTimeout(()=>setCopied(false),1800)}).catch(()=>setError(true))}>{copied?<Check size={12}/>:<Copy size={12}/>} {copied?'已复制':error?'复制失败':'Copy'}</button></div><pre ref={pre}>{children}</pre></div>;
}

export function Markdown({ text }: { text: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[[rehypeKatex, { strict: false, trust: false }]]}
        components={{
          pre: ({children}) => <CodeFrame>{children}</CodeFrame>,
          a: ({ href, children }) => (
            <a href={safeLink(href)} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          ),
          img: () => (
            <span className="inline-note">
              <ImageIcon size={13} /> 远程图片未自动加载
            </span>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
