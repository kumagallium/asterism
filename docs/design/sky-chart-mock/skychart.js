// skychart.js — 紙の星図（決定論配置・canvas・依存なし）
// 点: 種類＝星（大きさ＝件数の対数・面積比例）／ データセット＝星座（細い線で結ぶ）
//     共通の名前＝上の帯（青）／ 標準の語＝地平線（下の帯・琥珀）／ つながり（ハブ）＝星座のあいだの青い星
// 配置: 乱数も時刻も使わない。同じデータは同じ絵。
(function (global) {
  'use strict'

  // ── 決定論のハッシュ ───────────────────────────────────────────
  function fnv1a(str, seed) {
    let h = (seed === undefined ? 0x811c9dc5 : seed) >>> 0
    for (let i = 0; i < str.length; i++) {
      h ^= str.charCodeAt(i)
      h = Math.imul(h, 0x01000193) >>> 0
    }
    return h >>> 0
  }
  function hash01(str, salt) {
    const h = fnv1a(str + '|' + salt)
    const m = (h ^ (h >>> 16)) >>> 0
    return (m % 100000) / 100000
  }

  // ── 配置 ───────────────────────────────────────────────────────
  // data = { datasets:[{id,label,kinds:[{id,label,count,links:[kindId]}]}],
  //          names:[{id,label,kind:'class'|'property',members:[kindId],standard:stdId|null,relation:'sub'|'eq'}],
  //          standards:[{id,label,prefix}], hubs:[{id,label,members:[kindId]}] }
  function layout(data, W, H) {
    const nodes = new Map()
    const bands = {
      names: Math.round(H * 0.16),
      midTop: Math.round(H * 0.30),
      midBot: Math.round(H * 0.72),
      std: Math.round(H * 0.93),
    }
    const pad = 28

    // 星座（データセット）を横に並べる。幅は星の数で決める。
    const clusters = data.datasets.map((d) => {
      const n = d.kinds.length
      const w = Math.max(104, 46 * Math.sqrt(n) + 58)
      return { ds: d, w, h: bands.midBot - bands.midTop - 40 }
    })
    // 行に詰める（はみ出したら折り返す）
    const rows = []
    let cur = [], curW = 0
    const gap = 26
    for (const c of clusters) {
      if (cur.length && curW + c.w + gap > W - pad * 2) {
        rows.push(cur); cur = []; curW = 0
      }
      cur.push(c); curW += c.w + (cur.length > 1 ? gap : 0)
    }
    if (cur.length) rows.push(cur)
    const rowH = (bands.midBot - bands.midTop) / rows.length
    rows.forEach((row, ri) => {
      const total = row.reduce((s, c) => s + c.w, 0) + gap * (row.length - 1)
      let x = (W - total) / 2
      const cy = bands.midTop + rowH * (ri + 0.5)
      for (const c of row) {
        c.cx = x + c.w / 2
        c.cy = cy
        c.h = Math.min(c.h, rowH - 40)
        x += c.w + gap
      }
    })

    // 星座の中: ハッシュで初期位置 → 反発＋つながりの引力＋枠に収める（固定 70 回）
    for (const c of clusters) {
      const ks = c.ds.kinds
      const pts = ks.map((k, i) => {
        const a = hash01(k.id, 'angle') * Math.PI * 2
        const r = 0.25 + 0.6 * hash01(k.id, 'radius')
        return { k, x: c.cx + Math.cos(a) * r * c.w * 0.4, y: c.cy + Math.sin(a) * r * c.h * 0.42, vx: 0, vy: 0 }
      })
      const byId = new Map(pts.map((p) => [p.k.id, p]))
      for (let it = 0; it < 70; it++) {
        for (const p of pts) { p.vx = 0; p.vy = 0 }
        for (let i = 0; i < pts.length; i++) {
          for (let j = i + 1; j < pts.length; j++) {
            const a = pts[i], b = pts[j]
            let dx = b.x - a.x, dy = b.y - a.y
            let d2 = dx * dx + dy * dy
            if (d2 < 1) { dx = 0.5; dy = 0.3; d2 = 0.34 }
            const d = Math.sqrt(d2)
            const want = 72
            if (d < want) {
              const f = (want - d) / d * 0.5
              a.vx -= dx * f; a.vy -= dy * f; b.vx += dx * f; b.vy += dy * f
            }
          }
        }
        for (const p of pts) {
          for (const lid of p.k.links || []) {
            const q = byId.get(lid)
            if (!q) continue
            const dx = q.x - p.x, dy = q.y - p.y
            const d = Math.sqrt(dx * dx + dy * dy) || 1
            const want = 84
            const f = (d - want) / d * 0.08
            p.vx += dx * f; p.vy += dy * f; q.vx -= dx * f; q.vy -= dy * f
          }
        }
        for (const p of pts) {
          p.x += p.vx; p.y += p.vy
          const hx = c.w / 2 - 30, hy = c.h / 2 - 26
          p.x = Math.max(c.cx - hx, Math.min(c.cx + hx, p.x))
          p.y = Math.max(c.cy - hy, Math.min(c.cy + hy, p.y))
        }
      }
      for (const p of pts) {
        nodes.set(p.k.id, { id: p.k.id, type: 'kind', x: p.x, y: p.y, label: p.k.label, count: p.k.count, dataset: c.ds.id, links: p.k.links || [] })
      }
    }

    // 件数 → 半径（表示中の最小〜最大で対数・面積比例）
    const counts = [...nodes.values()].filter((n) => n.type === 'kind' && n.count > 0).map((n) => Math.log10(n.count))
    const lo = counts.length ? Math.min(...counts) : 0, hi = counts.length ? Math.max(...counts) : 1
    for (const n of nodes.values()) {
      if (n.type !== 'kind') continue
      const t = hi > lo && n.count > 0 ? (Math.log10(n.count) - lo) / (hi - lo) : 0.3
      n.r = 5 + 9 * Math.sqrt(t)
    }

    // 帯に並ぶもの: メンバーの重心の x に置き、近すぎたら押し広げる
    function placeBand(items, y, minGap, memberX) {
      const arr = items.map((it) => {
        const xs = memberX(it)
        const x = xs.length ? xs.reduce((s, v) => s + v, 0) / xs.length : pad + 60 + (W - pad * 2 - 120) * hash01(it.id, 'bandx')
        return { it, x }
      })
      arr.sort((a, b) => a.x - b.x)
      for (let i = 1; i < arr.length; i++) {
        if (arr[i].x - arr[i - 1].x < minGap) arr[i].x = arr[i - 1].x + minGap
      }
      const over = arr.length ? arr[arr.length - 1].x - (W - pad - 60) : 0
      if (over > 0) for (const a of arr) a.x -= over
      for (const a of arr) a.x = Math.max(pad + 60, a.x)
      return arr.map((a) => ({ ...a, y }))
    }
    const nameNodes = placeBand(data.names, bands.names, 130, (nm) =>
      nm.members.map((m) => nodes.get(m)).filter(Boolean).map((n) => n.x))
    for (const p of nameNodes) {
      nodes.set(p.it.id, { id: p.it.id, type: 'name', x: p.x, y: p.y, label: p.it.label, kind: p.it.kind, members: p.it.members, standard: p.it.standard || null, relation: p.it.relation || 'sub', cqs: p.it.cqs || [] })
    }
    const stdNodes = placeBand(data.standards, bands.std, 120, (s) => {
      const xs = []
      for (const n of nodes.values()) if (n.type === 'name' && n.standard === s.id) xs.push(n.x)
      for (const d of data.datasets) for (const k of d.kinds) if (k.standard === s.id) { const n = nodes.get(k.id); if (n) xs.push(n.x) }
      return xs
    })
    for (const p of stdNodes) {
      nodes.set(p.it.id, { id: p.it.id, type: 'std', x: p.x, y: p.y, label: p.it.label, prefix: p.it.prefix })
    }
    // ハブ: メンバーの重心。星座の内側に落ちたら少し下げる
    for (const h of data.hubs || []) {
      const ms = h.members.map((m) => nodes.get(m)).filter(Boolean)
      const x = ms.length ? ms.reduce((s, n) => s + n.x, 0) / ms.length : W / 2
      const y = bands.midBot + 36
      nodes.set(h.id, { id: h.id, type: 'hub', x, y, label: h.label, members: h.members })
    }
    return { nodes, clusters, bands }
  }

  // ── 描画 ───────────────────────────────────────────────────────
  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath()
    ctx.moveTo(x + r, y)
    ctx.arcTo(x + w, y, x + w, y + h, r)
    ctx.arcTo(x + w, y + h, x, y + h, r)
    ctx.arcTo(x, y + h, x, y, r)
    ctx.arcTo(x, y, x + w, y, r)
    ctx.closePath()
  }
  function star4(ctx, x, y, r) {
    ctx.beginPath()
    for (let i = 0; i < 8; i++) {
      const a = (Math.PI / 4) * i - Math.PI / 2
      const rr = i % 2 === 0 ? r : r * 0.42
      const px = x + Math.cos(a) * rr, py = y + Math.sin(a) * rr
      if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py)
    }
    ctx.closePath()
  }
  function withAlpha(hex, a) {
    const m = hex.replace('#', '')
    const n = m.length === 3 ? m.split('').map((c) => c + c).join('') : m
    const r = parseInt(n.slice(0, 2), 16), g = parseInt(n.slice(2, 4), 16), b = parseInt(n.slice(4, 6), 16)
    return 'rgba(' + r + ',' + g + ',' + b + ',' + a + ')'
  }
  const easeOutBack = (t) => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2) }

  function create(canvas, opts) {
    const o = Object.assign({
      colors: {
        paper: '#f7f9f4', grid: '#dde6da', bandText: '#728579', ink: '#16241a', body: '#33453a',
        entity: '#3a7a4c', entitySoft: '#e5f0e6', activity: '#356794', activitySoft: '#e4eef6',
        accent: '#b3722b', accentSoft: '#f6ecd9', surface: '#ffffff', faint: '#728579',
      },
      font: "'Hanken Grotesk','Zen Kaku Gothic New','Noto Sans JP',system-ui,sans-serif",
      mono: "'IBM Plex Mono',ui-monospace,Menlo,monospace",
      labels: { names: '共通の名前', mid: 'データ（星座）', std: '標準の語（地平線）', empty: 'まだ星がない' },
      onHover: null, onSelect: null, reducedMotion: false,
    }, opts || {})
    const ctx = canvas.getContext('2d')
    let data = o.data || { datasets: [], names: [], standards: [], hubs: [] }
    let L = null, W = 0, H = 0, dpr = 1
    let hover = null, selected = null
    const born = new Map() // id → 時刻（生まれたばかりの点）
    let raf = 0

    function size() {
      const rect = canvas.getBoundingClientRect()
      dpr = Math.min(2, global.devicePixelRatio || 1)
      W = Math.max(320, Math.round(rect.width))
      H = Math.max(360, Math.round(rect.height))
      canvas.width = W * dpr
      canvas.height = H * dpr
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      L = layout(data, W, H)
    }

    function neighborhood(id) {
      const s = new Set()
      if (!id || !L) return s
      const n = L.nodes.get(id)
      if (!n) return s
      s.add(id)
      if (n.type === 'kind') {
        for (const l of n.links) s.add(l)
        for (const m of L.nodes.values()) {
          if (m.type === 'kind' && m.links.includes(id)) s.add(m.id)
          if (m.type === 'name' && m.members.includes(id)) { s.add(m.id); if (m.standard) s.add(m.standard) }
          if (m.type === 'hub' && m.members.includes(id)) s.add(m.id)
        }
        for (const d of data.datasets) for (const k of d.kinds) if (k.id === id && k.standard) s.add(k.standard)
      } else if (n.type === 'name') {
        for (const m of n.members) s.add(m)
        if (n.standard) s.add(n.standard)
      } else if (n.type === 'hub') {
        for (const m of n.members) s.add(m)
      } else if (n.type === 'std') {
        for (const m of L.nodes.values()) if (m.type === 'name' && m.standard === id) s.add(m.id)
        for (const d of data.datasets) for (const k of d.kinds) if (k.standard === id) s.add(k.id)
      }
      return s
    }

    function draw(now) {
      if (!L) return
      const c = o.colors
      ctx.clearRect(0, 0, W, H)
      ctx.fillStyle = c.paper
      ctx.fillRect(0, 0, W, H)

      // 格子（星図の経線・緯線の気配）
      ctx.save()
      ctx.strokeStyle = withAlpha(c.grid, 0.9)
      ctx.lineWidth = 1
      for (let i = 1; i < 5; i++) {
        const y = (H / 5) * i
        ctx.beginPath()
        ctx.moveTo(0, y + 14)
        ctx.quadraticCurveTo(W / 2, y - 14, W, y + 14)
        ctx.stroke()
      }
      for (let i = 1; i < 7; i++) {
        const x = (W / 7) * i
        ctx.beginPath()
        ctx.moveTo(x, 0)
        ctx.quadraticCurveTo(x + (i - 3.5) * 10, H / 2, x, H)
        ctx.stroke()
      }
      ctx.restore()

      // 帯の名前
      ctx.save()
      ctx.font = '600 11px ' + o.font
      ctx.fillStyle = c.bandText
      ctx.textBaseline = 'middle'
      ctx.fillText(o.labels.names, 16, L.bands.names - 30)
      ctx.fillText(o.labels.mid, 16, L.bands.midTop - 10)
      ctx.fillText(o.labels.std, 16, L.bands.std - 28)
      // 地平線
      ctx.strokeStyle = withAlpha(c.accent, 0.35)
      ctx.lineWidth = 1.2
      ctx.beginPath()
      ctx.moveTo(0, L.bands.std - 16)
      ctx.quadraticCurveTo(W / 2, L.bands.std - 30, W, L.bands.std - 16)
      ctx.stroke()
      ctx.restore()

      const focus = hover || selected
      const near = neighborhood(focus)
      const dimmed = (id) => focus && !near.has(id)
      const alphaOf = (id) => (dimmed(id) ? 0.22 : 1)
      const scaleOf = (id) => {
        const t0 = born.get(id)
        if (t0 === undefined) return 1
        if (o.reducedMotion) return 1
        const t = Math.min(1, (now - t0) / 650)
        return Math.max(0.05, easeOutBack(t))
      }

      // 星座の線（データの中のつながり）と枠の名前
      for (const cl of L.clusters) {
        ctx.save()
        ctx.globalAlpha = focus && !cl.ds.kinds.some((k) => near.has(k.id)) ? 0.25 : 1
        ctx.font = '600 11px ' + o.font
        ctx.fillStyle = c.body
        ctx.textBaseline = 'alphabetic'
        ctx.fillText(cl.ds.label, cl.cx - cl.w / 2 + 8, cl.cy - cl.h / 2 + 2)
        ctx.strokeStyle = withAlpha(c.entity, 0.45)
        ctx.lineWidth = 1
        for (const k of cl.ds.kinds) {
          const a = L.nodes.get(k.id)
          for (const lid of k.links || []) {
            const b = L.nodes.get(lid)
            if (!a || !b) continue
            ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke()
          }
        }
        ctx.restore()
      }

      // 名前 ↔ 星、名前 ↔ 標準、星 ↔ 標準、ハブ ↔ 星 の線
      for (const n of L.nodes.values()) {
        if (n.type === 'name') {
          for (const m of n.members) {
            const t = L.nodes.get(m)
            if (!t) continue
            ctx.save()
            ctx.globalAlpha = Math.min(alphaOf(n.id), alphaOf(m)) * 0.9
            ctx.strokeStyle = c.activity
            ctx.lineWidth = near.has(n.id) && near.has(m) && focus ? 1.8 : 1
            ctx.beginPath()
            ctx.moveTo(t.x, t.y)
            ctx.bezierCurveTo(t.x, t.y - 70, n.x, n.y + 80, n.x, n.y + 14)
            ctx.stroke()
            // 矢じり（⊂ は名前の側へ）
            if (n.relation !== 'eq') {
              ctx.fillStyle = c.activity
              ctx.beginPath()
              ctx.moveTo(n.x, n.y + 13)
              ctx.lineTo(n.x - 4, n.y + 21)
              ctx.lineTo(n.x + 4, n.y + 21)
              ctx.closePath(); ctx.fill()
            }
            ctx.restore()
          }
          if (n.standard) {
            const s = L.nodes.get(n.standard)
            if (s) {
              ctx.save()
              ctx.globalAlpha = Math.min(alphaOf(n.id), alphaOf(s.id)) * 0.35
              ctx.strokeStyle = c.accent
              ctx.lineWidth = 1
              ctx.beginPath()
              ctx.moveTo(n.x + 10, n.y + 12)
              ctx.bezierCurveTo(n.x + 60, n.y + 160, s.x + 50, s.y - 160, s.x, s.y - 12)
              ctx.stroke()
              ctx.restore()
            }
          }
        }
        if (n.type === 'hub') {
          for (const m of n.members) {
            const t = L.nodes.get(m)
            if (!t) continue
            ctx.save()
            ctx.globalAlpha = Math.min(alphaOf(n.id), alphaOf(m)) * 0.9
            ctx.strokeStyle = c.activity
            ctx.lineWidth = 1.2
            ctx.beginPath(); ctx.moveTo(t.x, t.y); ctx.lineTo(n.x, n.y); ctx.stroke()
            ctx.restore()
          }
        }
      }
      for (const d of data.datasets) for (const k of d.kinds) {
        if (!k.standard) continue
        const a = L.nodes.get(k.id), s = L.nodes.get(k.standard)
        if (!a || !s) continue
        ctx.save()
        ctx.globalAlpha = Math.min(alphaOf(a.id), alphaOf(s.id)) * 0.7
        ctx.strokeStyle = c.accent
        ctx.lineWidth = 1
        ctx.beginPath()
        ctx.moveTo(a.x, a.y)
        ctx.bezierCurveTo(a.x, a.y + 90, s.x, s.y - 90, s.x, s.y - 12)
        ctx.stroke()
        ctx.restore()
      }

      // 星（種類）
      ctx.textAlign = 'center'
      for (const n of L.nodes.values()) {
        if (n.type !== 'kind') continue
        const sc = scaleOf(n.id)
        const r = n.r * sc
        ctx.save()
        ctx.globalAlpha = alphaOf(n.id)
        // 生まれたばかりの輪
        const t0 = born.get(n.id)
        if (t0 !== undefined && !o.reducedMotion) {
          const t = Math.min(1, (now - t0) / 1400)
          if (t < 1) {
            ctx.strokeStyle = withAlpha(c.entity, 0.5 * (1 - t))
            ctx.lineWidth = 2
            ctx.beginPath(); ctx.arc(n.x, n.y, r + 6 + 26 * t, 0, Math.PI * 2); ctx.stroke()
          }
        }
        ctx.fillStyle = c.entity
        ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, Math.PI * 2); ctx.fill()
        ctx.strokeStyle = c.surface
        ctx.lineWidth = 1.5
        ctx.stroke()
        if (focus === n.id) {
          ctx.strokeStyle = c.entity; ctx.lineWidth = 2
          ctx.beginPath(); ctx.arc(n.x, n.y, r + 4, 0, Math.PI * 2); ctx.stroke()
        }
        ctx.font = '500 11px ' + o.font
        ctx.fillStyle = c.ink
        ctx.textBaseline = 'top'
        ctx.lineWidth = 3
        ctx.strokeStyle = withAlpha(c.paper, 0.9)
        ctx.strokeText(n.label, n.x, n.y + r + 3)
        ctx.fillText(n.label, n.x, n.y + r + 3)
        if (n.count > 0) {
          ctx.font = '500 9.5px ' + o.mono
          ctx.fillStyle = c.faint
          const s = n.count >= 10000 ? (n.count / 10000).toFixed(n.count >= 100000 ? 0 : 1) + '万' : n.count.toLocaleString()
          ctx.strokeText(s + ' 件', n.x, n.y + r + 16)
          ctx.fillText(s + ' 件', n.x, n.y + r + 16)
        }
        ctx.restore()
      }
      // ハブ（つながり）
      for (const n of L.nodes.values()) {
        if (n.type !== 'hub') continue
        const sc = scaleOf(n.id)
        ctx.save()
        ctx.globalAlpha = alphaOf(n.id)
        ctx.fillStyle = c.activity
        star4(ctx, n.x, n.y, 11 * sc)
        ctx.fill()
        ctx.strokeStyle = c.surface; ctx.lineWidth = 1.5; ctx.stroke()
        ctx.font = '500 11px ' + o.font
        ctx.fillStyle = c.activity
        ctx.textBaseline = 'top'
        ctx.lineWidth = 3; ctx.strokeStyle = withAlpha(c.paper, 0.9)
        ctx.strokeText(n.label, n.x, n.y + 14)
        ctx.fillText(n.label, n.x, n.y + 14)
        ctx.restore()
      }
      // 共通の名前（上の帯）
      for (const n of L.nodes.values()) {
        if (n.type !== 'name') continue
        const sc = scaleOf(n.id)
        const empty = n.members.length === 0
        ctx.save()
        ctx.globalAlpha = alphaOf(n.id)
        ctx.font = '600 12px ' + o.font
        const tw = ctx.measureText(n.label).width
        const w = (tw + 26) * sc, h = 28 * sc
        const x = n.x - w / 2, y = n.y - h / 2
        const t0 = born.get(n.id)
        if (t0 !== undefined && !o.reducedMotion) {
          const t = Math.min(1, (now - t0) / 1400)
          if (t < 1) {
            ctx.strokeStyle = withAlpha(c.activity, 0.5 * (1 - t))
            ctx.lineWidth = 2
            roundRect(ctx, x - 6 - 20 * t, y - 6 - 20 * t, w + 12 + 40 * t, h + 12 + 40 * t, 20); ctx.stroke()
          }
        }
        roundRect(ctx, x, y, w, h, 14)
        ctx.fillStyle = empty ? c.activitySoft : c.activity
        ctx.fill()
        ctx.strokeStyle = c.activity
        ctx.lineWidth = focus === n.id ? 2.5 : 1.2
        ctx.stroke()
        ctx.fillStyle = empty ? c.activity : c.surface
        ctx.textBaseline = 'middle'
        ctx.fillText(n.label, n.x, n.y + 0.5)
        if (n.kind === 'property') {
          ctx.font = '500 9px ' + o.mono
          ctx.fillStyle = c.faint
          ctx.textBaseline = 'bottom'
          ctx.fillText('項目', n.x, y - 2)
        }
        if (empty) {
          ctx.font = '500 10px ' + o.font
          ctx.fillStyle = c.faint
          ctx.textBaseline = 'top'
          ctx.fillText(o.labels.empty, n.x, y + h + 3)
        } else if (n.standard && L.nodes.get(n.standard)) {
          const s = L.nodes.get(n.standard)
          ctx.font = '500 9.5px ' + o.mono
          ctx.fillStyle = c.accent
          ctx.textBaseline = 'top'
          ctx.fillText('≡ ' + s.prefix + ':' + s.label, n.x, y + h + 3)
        }
        ctx.restore()
      }
      // 標準の語（地平線）
      for (const n of L.nodes.values()) {
        if (n.type !== 'std') continue
        ctx.save()
        ctx.globalAlpha = alphaOf(n.id)
        ctx.font = '600 11px ' + o.font
        const tw = ctx.measureText(n.label).width
        const w = tw + 22, h = 26
        roundRect(ctx, n.x - w / 2, n.y - h / 2, w, h, 6)
        ctx.fillStyle = c.accentSoft
        ctx.fill()
        ctx.strokeStyle = withAlpha(c.accent, 0.7)
        ctx.lineWidth = focus === n.id ? 2 : 1
        ctx.stroke()
        ctx.fillStyle = c.accent
        ctx.textBaseline = 'middle'
        ctx.fillText(n.label, n.x, n.y + 0.5)
        ctx.font = '500 9px ' + o.mono
        ctx.fillStyle = c.faint
        ctx.textBaseline = 'top'
        ctx.fillText(n.prefix, n.x, n.y + h / 2 + 2)
        ctx.restore()
      }
      ctx.textAlign = 'start'
    }

    function hitTest(px, py) {
      if (!L) return null
      let best = null, bd = Infinity
      for (const n of L.nodes.values()) {
        let rr = n.type === 'kind' ? n.r + 8 : n.type === 'name' ? 44 : n.type === 'std' ? 40 : 16
        const dx = n.x - px, dy = n.y - py
        const d = Math.sqrt(dx * dx + dy * dy)
        if (d < rr && d < bd) { bd = d; best = n }
      }
      return best
    }

    function loop(now) {
      draw(now)
      let alive = false
      for (const t0 of born.values()) if (now - t0 < 1500) alive = true
      raf = alive ? global.requestAnimationFrame(loop) : 0
    }
    function render() { if (!raf) raf = global.requestAnimationFrame(loop) }

    function toLocal(ev) {
      const r = canvas.getBoundingClientRect()
      return { x: ev.clientX - r.left, y: ev.clientY - r.top }
    }
    canvas.addEventListener('mousemove', (ev) => {
      const p = toLocal(ev)
      const n = hitTest(p.x, p.y)
      const id = n ? n.id : null
      if (id !== hover) {
        hover = id
        canvas.style.cursor = id ? 'pointer' : 'default'
        if (o.onHover) o.onHover(n ? Object.assign({}, n) : null, p)
        render()
      }
    })
    canvas.addEventListener('mouseleave', () => { hover = null; if (o.onHover) o.onHover(null); render() })
    canvas.addEventListener('click', (ev) => {
      const p = toLocal(ev)
      const n = hitTest(p.x, p.y)
      selected = n ? n.id : null
      if (o.onSelect) o.onSelect(n ? Object.assign({}, n) : null)
      render()
    })

    const api = {
      setData(d) { data = d; size(); render() },
      resize() { size(); render() },
      grow(ids) {
        const now = global.performance.now()
        for (const id of ids) born.set(id, now)
        render()
      },
      select(id) { selected = id; render() },
      setQuiet(q) { o.reducedMotion = !!q; render() },
      node(id) { return L ? L.nodes.get(id) : null },
      nodes() { return L ? [...L.nodes.values()] : [] },
      destroy() { if (raf) global.cancelAnimationFrame(raf) },
    }
    size()
    render()
    return api
  }

  global.SkyChart = { create, layout, hash01 }
})(window)
