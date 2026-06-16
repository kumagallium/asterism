// Cost is computed from the per-model rate the USER enters (model-pricing has no
// built-in list prices): there is no programmatic API to fetch current model
// pricing — Anthropic's Models API exposes capabilities but not price, and
// OpenAI / Sakura have no pricing API — so any hard-coded table would silently go
// stale. The user sets input/output rates per model (find current prices on the
// provider's pricing page); a model with no rate shows tokens but no cost.
//
// The only thing kept here is the cache MULTIPLIER — a structural ratio of the
// input price, not a list price, so it doesn't drift with model releases.

/** Cache prices as a fraction of the input price, by provider. Anthropic prompt
 *  caching: read = 0.1× input, write (5-min ephemeral, what we use) = 1.25× input.
 *  The OpenAI-compatible path reports 0 cache tokens, so the multiplier is moot. */
export function cacheMultipliers(provider: string): { read: number; write: number } {
  if (provider === 'anthropic') return { read: 0.1, write: 1.25 }
  return { read: 0.5, write: 1 } // unused in practice (cache tokens are 0)
}
