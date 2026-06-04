# Vendored third-party library

`oxigraph.js` (renamed from `web.js`) and `web_bg.wasm` are the browser/wasm
build of **Oxigraph**, vendored here so the static demo runs with no CDN/network
dependency and no server.

- Project: https://github.com/oxigraph/oxigraph
- Package: `oxigraph` (npm), version **0.5.8**
- License: MIT OR Apache-2.0 (© Oxigraph contributors)

These files are unmodified except for the rename of `web.js` → `oxigraph.js`
(the default init still resolves `web_bg.wasm` relative to its own URL, so the
wasm filename is kept). Regenerate with:

```bash
npm pack oxigraph        # then extract package/web.js + package/web_bg.wasm
```
