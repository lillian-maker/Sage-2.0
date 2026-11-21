import { Fragment } from 'react';
import './knowledge.css';

export interface KnowledgeDoc {
  title: string;
  paragraphs: string[];
  page: number;
  pageCount: number;
}

export interface DocumentPreviewProps {
  doc: KnowledgeDoc;
  highlights?: string[];
}

function highlight(text: string, words: string[]) {
  if (words.length === 0) return text;
  const re = new RegExp('(' + words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') + ')');
  return text.split(re).map((p, i) => (words.includes(p) ? <mark key={i}>{p}</mark> : <Fragment key={i}>{p}</Fragment>));
}

export function DocumentPreview({ doc, highlights = [] }: DocumentPreviewProps) {
  return (
    <article className="sg-docpreview">
      <h2>{doc.title}</h2>
      {doc.paragraphs.map((p, i) => (
        <p key={i}>{highlight(p, highlights)}</p>
      ))}
      <div className="sg-docpreview__page">
        第 {doc.page} / {doc.pageCount} 页
      </div>
    </article>
  );
}
