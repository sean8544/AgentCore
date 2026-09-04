/** Strip the YAML frontmatter block from SKILL.md content (if present). */
export function stripFrontmatter(content: string): string {
  const match = content.match(/^---\s*\n[\s\S]*?\n---\s*\n/);
  return match ? content.slice(match[0].length) : content;
}
