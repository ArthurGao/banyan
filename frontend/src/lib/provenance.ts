/** The step that wrote the answer, from `role:provider:model[,…]` (the last entry): `answer.from_kb · claude-sub`.
 * The whole chain stays in the tooltip. */
export function answeredBy(provenance: string): string {
  const last = provenance.split(',').at(-1)?.trim() ?? provenance
  // The model id may itself contain colons (qwen3:8b): everything after the provider.
  const [role, , ...model] = last.split(':')
  return model.length > 0 ? `${role ?? ''} · ${model.join(':')}` : last
}
