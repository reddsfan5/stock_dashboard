# three.js 0.160.0 (vendored)

Local fallback for `output/sector_corr_cloud.html` (served at `/assets/vendor/three/`).
The page tries these files first and only falls back to jsDelivr (`three@0.160.0/+esm`) if they fail.

- `three.module.min.js` — `three@0.160.0/build/three.module.min.js`, unmodified.
- `OrbitControls.js` — `three@0.160.0/examples/jsm/controls/OrbitControls.js`; the only change is
  `from 'three'` → `from './three.module.min.js'`, so no import map is needed.
- `LICENSE` — MIT, three.js authors.
