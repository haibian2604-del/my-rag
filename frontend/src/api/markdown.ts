import DOMPurify from "dompurify";
import { marked } from "marked";

marked.setOptions({ async: false, breaks: true });

export function renderMarkdown(text: string): string {
  const html = marked.parse(text) as string;
  return DOMPurify.sanitize(html);
}
