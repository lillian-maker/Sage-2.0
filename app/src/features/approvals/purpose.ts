/**
 * The expert's purpose sentence names the queried range (“…类目前 50 名竞品…”). After 修改范围 the server rewrites
 * scope_text but keeps purpose_text as requested, so the sentence follows scope_params.top_n here (2732:797, 2736:1011).
 */
export function scopedPurpose(purpose: string, scopeParams: Record<string, unknown> | null | undefined): string {
  const topN = scopeParams?.top_n;
  return typeof topN === 'number' ? purpose.replace(/前\s*\d+\s*名/, `前 ${topN} 名`) : purpose;
}
