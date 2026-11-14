/* Copyright (c) 2026 Ederland Studio — MIT.
 * Origem: ederland/Agentes_IA_3D@073cbc2fdd9c52428e67935e35e0e616c892a258:app/src/office/navgrid.js
 * alterado: ver static/oficina/README.md
 */
// Cuadrícula de navegación con A*: los personajes esquivan paredes y muebles
// y entran a las salas solo por sus puertas.
export class NavGrid {
  constructor(x0, z0, x1, z1, cell = 0.2) {
    this.x0 = x0; this.z0 = z0
    this.cell = cell
    this.w = Math.ceil((x1 - x0) / cell)
    this.h = Math.ceil((z1 - z0) / cell)
    this.blocked = new Uint8Array(this.w * this.h)
  }

  toCell(x, z) {
    return [
      Math.min(this.w - 1, Math.max(0, Math.floor((x - this.x0) / this.cell))),
      Math.min(this.h - 1, Math.max(0, Math.floor((z - this.z0) / this.cell))),
    ]
  }
  toWorld(cx, cz) {
    return [this.x0 + (cx + 0.5) * this.cell, this.z0 + (cz + 0.5) * this.cell]
  }

  // Bloquea un rectángulo (centro, ancho en x, profundidad en z, rotación en y) con margen
  blockRect(cx, cz, w, d, rot = 0, pad = 0.28) {
    const c = Math.cos(rot), s = Math.sin(rot)
    const hw = w / 2 + pad, hd = d / 2 + pad
    const ext = Math.abs(hw * c) + Math.abs(hd * s)
    const ezt = Math.abs(hw * s) + Math.abs(hd * c)
    const [ax, az] = this.toCell(cx - ext, cz - ezt)
    const [bx, bz] = this.toCell(cx + ext, cz + ezt)
    for (let gz = az; gz <= bz; gz++) for (let gx = ax; gx <= bx; gx++) {
      const [wx, wz] = this.toWorld(gx, gz)
      const dx = wx - cx, dz = wz - cz
      // Coordenadas locales del rectángulo rotado
      const lx = dx * c - dz * s, lz = dx * s + dz * c
      if (Math.abs(lx) <= hw && Math.abs(lz) <= hd) this.blocked[gz * this.w + gx] = 1
    }
  }

  free(gx, gz) {
    return gx >= 0 && gz >= 0 && gx < this.w && gz < this.h && !this.blocked[gz * this.w + gx]
  }

  nearestFree(gx, gz) {
    if (this.free(gx, gz)) return [gx, gz]
    for (let r = 1; r < 25; r++) {
      for (let dz = -r; dz <= r; dz++) for (let dx = -r; dx <= r; dx++) {
        if (Math.max(Math.abs(dx), Math.abs(dz)) !== r) continue
        if (this.free(gx + dx, gz + dz)) return [gx + dx, gz + dz]
      }
    }
    return [gx, gz]
  }

  lineFree(a, b) {
    const steps = Math.ceil(Math.hypot(b[0] - a[0], b[1] - a[1]) / (this.cell * 0.5))
    for (let i = 0; i <= steps; i++) {
      const t = steps ? i / steps : 0
      const [gx, gz] = this.toCell(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
      if (!this.free(gx, gz)) return false
    }
    return true
  }

  // Devuelve puntos [x, z] desde `from` hasta `to` (excluye el punto de partida)
  findPath(from, to) {
    const [sx, sz] = this.nearestFree(...this.toCell(from[0], from[1]))
    const [tx, tz] = this.nearestFree(...this.toCell(to[0], to[1]))
    const W = this.w
    const start = sz * W + sx, goal = tz * W + tx
    const g = new Float32Array(W * this.h).fill(Infinity)
    const came = new Int32Array(W * this.h).fill(-1)
    const closed = new Uint8Array(W * this.h)
    const heap = [[0, start]]
    g[start] = 0
    const h = (i) => Math.hypot((i % W) - tx, Math.floor(i / W) - tz)
    const push = (item) => {
      heap.push(item)
      let i = heap.length - 1
      while (i > 0) { const p = (i - 1) >> 1; if (heap[p][0] <= heap[i][0]) break; [heap[p], heap[i]] = [heap[i], heap[p]]; i = p }
    }
    const pop = () => {
      const top = heap[0], last = heap.pop()
      if (heap.length) {
        heap[0] = last
        let i = 0
        for (;;) {
          const l = 2 * i + 1, r = l + 1
          let m = i
          if (l < heap.length && heap[l][0] < heap[m][0]) m = l
          if (r < heap.length && heap[r][0] < heap[m][0]) m = r
          if (m === i) break
          ;[heap[m], heap[i]] = [heap[i], heap[m]]; i = m
        }
      }
      return top
    }
    const dirs = [[1, 0, 1], [-1, 0, 1], [0, 1, 1], [0, -1, 1], [1, 1, 1.414], [1, -1, 1.414], [-1, 1, 1.414], [-1, -1, 1.414]]
    let found = start === goal
    while (heap.length && !found) {
      const [, cur] = pop()
      if (closed[cur]) continue
      closed[cur] = 1
      if (cur === goal) { found = true; break }
      const cx = cur % W, cz = Math.floor(cur / W)
      for (const [dx, dz, cost] of dirs) {
        const nx = cx + dx, nz = cz + dz
        if (!this.free(nx, nz)) continue
        if (dx && dz && (!this.free(cx + dx, cz) || !this.free(cx, cz + dz))) continue
        const ni = nz * W + nx
        const ng = g[cur] + cost
        if (ng < g[ni]) { g[ni] = ng; came[ni] = cur; push([ng + h(ni), ni]) }
      }
    }
    if (!found) return [to]

    const cells = []
    for (let c = goal; c !== -1; c = came[c]) cells.push(this.toWorld(c % W, Math.floor(c / W)))
    cells.reverse()

    // Suavizado: saltar puntos intermedios mientras haya línea de vista
    const pts = [from, ...cells]
    const smooth = [pts[0]]
    let i = 0
    while (i < pts.length - 1) {
      let j = pts.length - 1
      while (j > i + 1 && !this.lineFree(pts[i], pts[j])) j--
      smooth.push(pts[j])
      i = j
    }
    smooth.push(to)
    return smooth.slice(1)
  }
}
