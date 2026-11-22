import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const step = 6
function inside(x, y, ring) {
  let hit = false
  for (let i = 0, j = ring.length - 2; i < ring.length; j = i, i += 2) {
    const ax = ring[i], ay = ring[i + 1], bx = ring[j], by = ring[j + 1]
    if ((ay > y) !== (by > y) && x < ax + (bx - ax) * (y - ay) / (by - ay)) hit = !hit
  }
  return hit
}

// Reverse flood fill gives all approach directions for one destination.
export class Arena {
  constructor(dir) {
    const world = JSON.parse(readFileSync(join(dir, 'v2/world.json'), 'utf8'))
    const terrain = readFileSync(join(dir, 'v2/terrain.i16'))
    const t = world.terrain, b = world.bounds
    this.x = b.minX
    this.y = b.minY
    this.cols = Math.ceil((b.maxX - b.minX) / step)
    this.rows = Math.ceil((b.maxY - b.minY) / step)
    const n = this.cols * this.rows
    this.blocked = new Uint8Array(n)
    this.next = new Int32Array(n)
    this.queue = new Int32Array(n)
    this.goal = -1
    for (const p of world.prisms) {
      const xs = p.ring.filter((_, i) => i % 2 === 0)
      const ys = p.ring.filter((_, i) => i % 2 === 1)
      const lo = this.cell([Math.min(...xs)-step, Math.min(...ys)-step])
      const hi = this.cell([Math.max(...xs)+step, Math.max(...ys)+step])
      for (let row = Math.floor(lo/this.cols); row <= Math.floor(hi/this.cols); row++) {
        for (let col = lo%this.cols; col <= hi%this.cols; col++) {
          const id = row * this.cols + col
          const [x,y] = this.point(id)
          const i = Math.min(t.nx-1, Math.max(0, Math.round((x-t.minX)/t.step)))
          const j = Math.min(t.ny-1, Math.max(0, Math.round((y-t.minY)/t.step)))
          const ground = terrain.readInt16LE(2*(j*t.nx+i)) * t.scale
          if (p.base > ground+10 || p.top < ground+1.2) continue
          for (const [dx,dy] of [[0,0],[2,2],[-2,2],[2,-2],[-2,-2]]) {
            if (inside(x+dx,y+dy,p.ring) && !(p.holes??[]).some(h=>inside(x+dx,y+dy,h))) {
              this.blocked[id] = 1
              break
            }
          }
        }
      }
    }
    // Terrain cliffs: moving uphill across a step steeper than walkable is
    // impossible. steep[id*4+k] = 1 when leaving cell id in direction k
    // (0:-x 1:+x 2:-y 3:+y) climbs too steeply.
    const h = (x, y) => {
      const i = Math.min(t.nx-1, Math.max(0, Math.round((x-t.minX)/t.step)))
      const j = Math.min(t.ny-1, Math.max(0, Math.round((y-t.minY)/t.step)))
      return terrain.readInt16LE(2*(j*t.nx+i)) * t.scale
    }
    this.steep = new Uint8Array(n*4)
    const dirs = [[-1,0],[1,0],[0,-1],[0,1]]
    for (let id = 0; id < n; id++) {
      const [x,y] = this.point(id)
      for (let k = 0; k < 4; k++) {
        const [ux,uy] = dirs[k]
        let prev = h(x,y), climb = 0
        for (let s = 1; s <= step; s++) {
          const cur = h(x+ux*s, y+uy*s)
          const rise = cur - prev
          if (rise > 1.25) { this.steep[id*4+k] = 1; break }
          climb = rise > 0 ? climb + rise : 0
          if (climb > 4.5 && climb > 1.1*s) { this.steep[id*4+k] = 1; break }
          prev = cur
        }
      }
    }
  }
  cell([x,y]) {
    const col = Math.min(this.cols-1, Math.max(0, Math.floor((x-this.x)/step)))
    const row = Math.min(this.rows-1, Math.max(0, Math.floor((y-this.y)/step)))
    return row*this.cols+col
  }
  point(id) {
    return [this.x+(id%this.cols+.5)*step, this.y+(Math.floor(id/this.cols)+.5)*step]
  }
  nearest(id) {
    if (!this.blocked[id]) return id
    const col = id%this.cols, row = Math.floor(id/this.cols)
    for (let r=1; r<=18; r++) {
      for (let dy=-r; dy<=r; dy++) for (let dx=-r; dx<=r; dx++) {
        if (Math.max(Math.abs(dx),Math.abs(dy))!==r) continue
        const x=col+dx, y=row+dy
        if (x>=0 && x<this.cols && y>=0 && y<this.rows && !this.blocked[y*this.cols+x]) return y*this.cols+x
      }
    }
    return -1
  }
  reachableNear(start,dest) {
    if (!this.seen) { this.seen=new Uint8Array(this.next.length); this.fq=new Int32Array(this.next.length) }
    const key=start*1e6+dest
    if (this.subKey===key) return this.subVal
    this.seen.fill(0)
    const dc=dest%this.cols, dr=Math.floor(dest/this.cols)
    let best=start, bestD=Infinity, tail=1
    this.fq[0]=start; this.seen[start]=1
    for (let head=0; head<tail; head++) {
      const id=this.fq[head], col=id%this.cols, row=Math.floor(id/this.cols)
      const d=(col-dc)**2+(row-dr)**2
      if (d<bestD) { bestD=d; best=id }
      const nb=[col>0?id-1:-1,col+1<this.cols?id+1:-1,id-this.cols,id+this.cols]
      for (let k=0;k<4;k++) {
        const o=nb[k]
        if (o<0 || o>=this.next.length || this.blocked[o] || this.seen[o] || this.steep[id*4+k]) continue
        this.seen[o]=1; this.fq[tail++]=o
      }
    }
    this.subKey=key; this.subVal=best
    return best
  }
  steer(from,to,depth=0) {
    if (Math.hypot(from[0]-to[0],from[1]-to[1]) < 9) return to
    const orig=this.nearest(this.cell(to))
    if (orig<0) return to
    if (!this.redirect || this.redirect.size>300) this.redirect=new Map()
    let dest=this.redirect.get(orig) ?? orig
    let redirected=dest!==orig
    if (dest!==this.goal) {
      this.goal=dest
      this.next.fill(-1)
      this.next[dest]=dest
      this.queue[0]=dest
      let tail=1
      for (let head=0; head<tail; head++) {
        const id=this.queue[head], col=id%this.cols
        // neighbour `other` moves to `id`: direction from other to id
        const nb=[col>0?id-1:-1,col+1<this.cols?id+1:-1,id-this.cols,id+this.cols]
        const back=[1,0,3,2]
        for (let k=0;k<4;k++) {
          const other=nb[k]
          if (other<0 || other>=this.next.length || this.blocked[other] || this.next[other]>=0) continue
          if (this.steep[other*4+back[k]]) continue
          this.next[other]=id
          this.queue[tail++]=other
        }
      }
    }
    const start=this.cell(from)
    if (this.blocked[start]) {
      const clear=this.nearest(start)
      return clear<0?to:this.point(clear)
    }
    let next=this.next[start]
    if (start===dest) return redirected?this.point(dest):to
    if (next<0) {
      // destination unreachable from here: head for the reachable cell nearest to it
      const sub=this.reachableNear(start,orig)
      if (sub<0 || sub===start || depth>1) return to
      this.redirect.set(orig,sub)
      return this.steer(from,this.point(sub),depth+1)
    }
    return this.point(next)
  }
}
