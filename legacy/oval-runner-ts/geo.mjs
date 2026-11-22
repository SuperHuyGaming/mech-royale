import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// Line-of-sight model: a 2 m raster of solid intervals [base, top] built from
// the map prisms, plus the terrain height field. Rays are marched through it.
const RES = 2

function inside(x, y, ring) {
  let hit = false
  for (let i = 0, j = ring.length - 2; i < ring.length; j = i, i += 2) {
    const ax = ring[i], ay = ring[i + 1], bx = ring[j], by = ring[j + 1]
    if ((ay > y) !== (by > y) && x < ax + (bx - ax) * (y - ay) / (by - ay)) hit = !hit
  }
  return hit
}

export class World {
  constructor(dir) {
    const world = JSON.parse(readFileSync(join(dir, 'v2/world.json'), 'utf8'))
    const raw = readFileSync(join(dir, 'v2/terrain.i16'))
    const t = world.terrain
    this.t = t
    this.h = new Int16Array(raw.buffer.slice(raw.byteOffset, raw.byteOffset + t.nx * t.ny * 2))
    const b = world.bounds
    this.b = b
    this.x0 = b.minX; this.y0 = b.minY
    this.nx = Math.ceil((b.maxX - b.minX) / RES)
    this.ny = Math.ceil((b.maxY - b.minY) / RES)
    const n = this.nx * this.ny
    this.base = new Float32Array(n).fill(1e9)
    this.top = new Float32Array(n).fill(-1e9)
    for (const p of world.prisms) {
      let minx = Infinity, maxx = -Infinity, miny = Infinity, maxy = -Infinity
      for (let i = 0; i < p.ring.length; i += 2) {
        minx = Math.min(minx, p.ring[i]); maxx = Math.max(maxx, p.ring[i])
        miny = Math.min(miny, p.ring[i + 1]); maxy = Math.max(maxy, p.ring[i + 1])
      }
      const c0 = Math.max(0, Math.floor((minx - this.x0) / RES)), c1 = Math.min(this.nx - 1, Math.floor((maxx - this.x0) / RES))
      const r0 = Math.max(0, Math.floor((miny - this.y0) / RES)), r1 = Math.min(this.ny - 1, Math.floor((maxy - this.y0) / RES))
      for (let r = r0; r <= r1; r++) for (let c = c0; c <= c1; c++) {
        const x = this.x0 + (c + .5) * RES, y = this.y0 + (r + .5) * RES
        if (!inside(x, y, p.ring)) continue
        if ((p.holes ?? []).some(h => inside(x, y, h))) continue
        const id = r * this.nx + c
        if (p.base < this.base[id]) this.base[id] = p.base
        if (p.top > this.top[id]) this.top[id] = p.top
      }
    }
  }
  ground(x, y) {
    const t = this.t
    let fx = (x - t.minX) / t.step, fy = (y - t.minY) / t.step
    fx = Math.min(t.nx - 1.001, Math.max(0, fx)); fy = Math.min(t.ny - 1.001, Math.max(0, fy))
    const i = Math.floor(fx), j = Math.floor(fy), u = fx - i, v = fy - j
    const k = j * t.nx + i
    const h = this.h
    return ((h[k] * (1 - u) + h[k + 1] * u) * (1 - v) + (h[k + t.nx] * (1 - u) + h[k + t.nx + 1] * u) * v) * t.scale
  }
  solidAt(x, y, z) {
    const c = Math.floor((x - this.x0) / RES), r = Math.floor((y - this.y0) / RES)
    if (c < 0 || r < 0 || c >= this.nx || r >= this.ny) return true
    const id = r * this.nx + c
    if (z >= this.base[id] && z <= this.top[id]) return true
    return z < this.ground(x, y) - 0.2
  }
  // True when the segment a->b is clear (ignores the first/last `skip` meters).
  los(a, b, skip = 2.5) {
    const dx = b[0] - a[0], dy = b[1] - a[1], dz = b[2] - a[2]
    const L = Math.hypot(dx, dy, dz)
    if (L < 2 * skip) return true
    const step = 1.5
    for (let s = skip; s < L - skip; s += step) {
      const f = s / L
      if (this.solidAt(a[0] + dx * f, a[1] + dy * f, a[2] + dz * f)) return false
    }
    return true
  }
}
