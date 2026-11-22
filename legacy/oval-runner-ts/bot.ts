import { run } from './mechfight.ts'
import { Arena } from './nav.mjs'
import { World } from './geo.mjs'

const dist=(a,b)=>Math.hypot(a[0]-b[0],a[1]-b[1])
const wrap=a=>Math.atan2(Math.sin(a),Math.cos(a))
const clamp=(v,lo,hi)=>Math.max(lo,Math.min(hi,v))

function mulberry32(a){return function(){a|=0;a=a+0x6D2B79F5|0;let t=Math.imul(a^a>>>15,1|a);t=t+Math.imul(t^t>>>7,61|t)^t;return((t^t>>>14)>>>0)/4294967296}}

// Estimate when the moving circle will reach a game position.
function zoneTimeRemaining(zone,pos,now) {
  const start=Math.max(now,zone.shrink_start)
  const margin=(f)=>{
    const x=zone.center[0]+f*(zone.next_center[0]-zone.center[0])
    const y=zone.center[1]+f*(zone.next_center[1]-zone.center[1])
    const r=zone.radius+f*(zone.next_radius-zone.radius)
    return r-Math.hypot(pos[0]-x,pos[1]-y)
  }
  if (margin(0)<0) return 0
  if (margin(1)>=0) return Infinity
  let lo=0,hi=1
  for (let i=0;i<9;i++){const mid=(lo+hi)/2; if(margin(mid)>=0) lo=mid; else hi=mid}
  return start-now+lo*Math.max(0,zone.shrink_end-start)
}

// Finish a nearly banked supply refill when braking keeps us inside the ring.
function canFinishCapture(obs, config, lastHurt) {
  const me = obs.self
  if (!me.grounded || !me.in_zone || me.hp > .85 * me.max_hp ||
      obs.t - lastHurt < .6 || obs.projectiles.length) return false
  const dt = config.match.decide_every / config.match.tick_hz
  const acceleration = me.stats.ground_accel
  if (!(dt > 0 && acceleration > 0)) return false
  return obs.drops.some(drop => {
    if (drop.holder !== me.id || drop.lands_in > 0 || drop.progress <= 0) return false
    const remaining = drop.capture_s - drop.progress
    if (remaining <= 0 || remaining > 1 || obs.t + remaining + dt >= config.match.duration_s) return false
    const speed = Math.hypot(me.vel[0], me.vel[1])
    const brakingDistance = speed * dt + speed * speed / (2 * acceleration)
    if (dist(me.pos, drop.pos) + brakingDistance >= drop.radius - .1) return false
    if (zoneTimeRemaining(obs.zone, me.pos, obs.t) <= remaining + dt ||
        zoneTimeRemaining(obs.zone, drop.pos, obs.t) <= remaining + dt) return false
    return obs.mechs.every(rival => rival.kind !== 'mech' ||
      dist(rival.pos, drop.pos) > drop.radius + (remaining + dt) *
      Math.max(1.6 * config.mech.boost_speed, Math.hypot(rival.vel[0], rival.vel[1])))
  })
}

run(hello=>{
  const C=hello.config
  const arena=new Arena(hello.map.dir)
  const world=new World(hello.map.dir)
  const rand=mulberry32(hello.seed>>>0)
  const sizePer=C.cubes.size_per_cube
  let lastPos=null, sampleTime=-1, stuckUntil=-1, previousMove=false, sprint=false
  let lastHurt=-Infinity, lastDealt=-Infinity, hurtBearing=0, hurtBy=-1
  let alertBearing=0, alertT=-Infinity
  let preferredId=-1
  let strafeDir=1, strafeFlipAt=0
  let hide=null, hideAt=-Infinity
  let laserHeldSince=-1
  const remembered=new Map()
  const crateMem=new Map()
  const lightningUntil=Number(process.env.LT_UNTIL??0)
  const pausedUntil=new Map()
  let pursuit={id:-1,best:Infinity,progressAt:0}
  const phase=(hello.slot+1)*2.39996

  function centerOf(m){
    if (m.kind==='npc') return [m.pos[0],m.pos[1],m.pos[2]+1.75]
    const s=1+(m.cubes??0)*sizePer
    return [m.pos[0],m.pos[1],m.pos[2]+4.5*s]
  }
  // Aim [yaw,pitch] for a projectile of speed v from muzzle M at a moving target.
  function leadAim(M,m,v,extra=0){
    const P=centerOf(m), V=m.vel??[0,0,0]
    const vz=Math.abs(V[2]??0)>1?V[2]:0
    let t=dist(M,P)/v+extra
    let Q=P
    for (let i=0;i<4;i++){
      Q=[P[0]+V[0]*t,P[1]+V[1]*t,P[2]+vz*t]
      // a falling/rising mech lands; don't predict through the ground
      const g=world.ground(Q[0],Q[1])+ (m.kind==='npc'?1.75:4.5)
      if (Q[2]<g) Q[2]=g
      t=Math.hypot(Q[0]-M[0],Q[1]-M[1],Q[2]-M[2])/v+extra
    }
    const h=Math.hypot(Q[0]-M[0],Q[1]-M[1])
    return {yaw:Math.atan2(Q[1]-M[1],Q[0]-M[0]),pitch:Math.atan2(Q[2]-M[2],h),point:Q,range:h}
  }
  function freeAhead(pos,dir,len){
    for (const s of [3,len*.5,len]){
      const x=pos[0]+dir[0]*s, y=pos[1]+dir[1]*s
      if (arena.blocked[arena.cell([x,y])]) return false
    }
    return true
  }
  // Find a nearby spot the enemy cannot see.
  function findHide(pos,enemy,zone,now){
    const E=[enemy.pos[0],enemy.pos[1],enemy.pos[2]+8]
    let best=null,bestCost=Infinity
    const away=Math.atan2(pos[1]-enemy.pos[1],pos[0]-enemy.pos[0])
    for (let k=0;k<16;k++){
      const a=away+(k-7.5)*Math.PI/8
      for (const r of [10,20,32,46]){
        const p=[pos[0]+Math.cos(a)*r,pos[1]+Math.sin(a)*r]
        if (arena.blocked[arena.cell(p)]) continue
        if (dist(p,zone.center)>zone.radius-8) continue
        if (zoneTimeRemaining(zone,p,now)<20) continue
        const z=world.ground(p[0],p[1])
        if (world.los(E,[p[0],p[1],z+5],1)) continue
        const cost=r-0.4*dist(p,enemy.pos)+Math.abs(wrap(a-away))*8
        if (cost<bestCost){bestCost=cost;best=p}
      }
    }
    return best
  }

  return {name:'Oval Runner',act(obs) {
    const me=obs.self, pos=me.pos, zone=obs.zone, now=obs.t
    const speed=me.stats.walk_speed, hp=me.hp/me.max_hp, stamina=me.energy/me.max_energy
    const myScale=1+me.cubes*sizePer
    const muzzleZ=pos[2]+(me.stats.muzzle_height??7)
    const eye=[pos[0],pos[1],pos[2]+(me.stats.eye_height??8)]
    const gun=me.weapons[0]?.kind==='gun'?me.weapons[0]:(me.weapons[1]?.kind==='gun'?me.weapons[1]:null)
    const gunSlot=me.weapons[0]?.kind==='gun'?0:1
    const secSlot=gunSlot===0?1:0
    const secondary=me.weapons[secSlot]

    for (const e of obs.events){
      if (e.type==='damaged' && e.weapon!=='zone'){lastHurt=now; if(e.bearing!=null) hurtBearing=e.bearing; hurtBy=e.by??-1}
      if (e.type==='hit') lastDealt=now
    }
    for (const s of obs.sounds){
      if ((s.kind==='gun'||s.kind==='laser'||s.kind==='laser_charge'||s.kind==='lightning'||s.kind==='mortar'||s.kind==='boost'||s.kind==='step') && s.dist!=='far'){alertBearing=s.bearing;alertT=now}
    }

    const foes=obs.mechs.filter(m=>m.kind==='mech')
    const npcs=obs.mechs.filter(m=>m.kind==='npc')
    const info=new Map()
    for (const m of foes.concat(npcs)){
      const c=centerOf(m)
      info.set(m.id,{d:dist(pos,m.pos),los:world.los([pos[0],pos[1],muzzleZ],c,3.2)})
    }
    const closest=foes.reduce((a,b)=>!a||info.get(b.id).d<info.get(a.id).d?b:a,null)
    const danger=closest?info.get(closest.id).d:Infinity
    const visibleFoes=foes.filter(m=>info.get(m.id).los)

    // pickups memory
    for (const p of obs.pickups) remembered.set(p.id,{item:p,seen:now})
    for (const [id,entry] of remembered){
      const missingNearby=dist(pos,entry.item.pos)<8 && !obs.pickups.some(p=>p.id===id)
      if (missingNearby || now-entry.seen>20) remembered.delete(id)
    }
    for (const [id,end] of pausedUntil) if (now>=end) pausedUntil.delete(id)
    for (const c of obs.crates) crateMem.set(c.id,{pos:c.pos,hp:c.hp,seen:now})
    for (const [id,c] of crateMem){
      const d=dist(pos,c.pos)
      if (d<40 && !obs.crates.some(x=>x.id===id) && world.los(eye,[c.pos[0],c.pos[1],c.pos[2]+1.2],1)) crateMem.delete(id)
    }
    for (const e of obs.events) if (e.type==='hit' && e.target>=1000 && e.target<5000){const c=crateMem.get(e.target); if(c) c.hp-=e.amount}

    const left=zoneTimeRemaining(zone,pos,now)
    const outsideNext=Math.max(0,dist(pos,zone.next_center)-zone.next_radius)
    const rush=!me.in_zone || (outsideNext>0 && outsideNext*1.5/speed+5>=left)

    // ---------- target selection ----------
    const cost=m=>{
      const i=info.get(m.id)
      const ws=m.weapons??[]
      const cook=secondary?.kind==='laser' && ws.includes('mortar') ? 30 : 0
      const aimingAtMe=m.charging && Math.abs(wrap((m.aim?.[0]??0)-Math.atan2(pos[1]-m.pos[1],pos[0]-m.pos[0])))<.12 ? 25 : 0
      return i.d+90*(m.hp??m.max_hp??1)/(m.max_hp??1)+(i.los?0:70)-(m.id===preferredId?20:0)-(m.id===hurtBy&&now-lastHurt<3?25:0)-cook-aimingAtMe
    }
    const opponent=foes.reduce((best,m)=>!best||cost(m)<cost(best)?m:best,null)
    preferredId=opponent?.id??-1
    // farm NPC drones (2 cubes each) when no mech is around
    let npcTarget=null
    if (!opponent && !rush){
      for (const n of npcs){
        const i=info.get(n.id)
        const attacking=hurtBy===n.id && now-lastHurt<4
        if ((attacking || (hp>.8 && i.d<80)) && i.los && (!npcTarget || i.d<info.get(npcTarget.id).d)) npcTarget=n
      }
    }
    const target=opponent??npcTarget
    const tInfo=target?info.get(target.id):null
    const engaged=!!opponent

    // ---------- goal selection (non-combat) ----------
    const angle=closest?Math.atan2(pos[1]-closest.pos[1],pos[0]-closest.pos[0]):phase+Math.floor(now/18)*.65
    const orbit=Math.max(0,zone.next_radius-8)*.55
    let goal=[zone.next_center[0]+Math.cos(angle)*orbit,zone.next_center[1]+Math.sin(angle)*orbit]
    let value=0, capture=false, selectedId=-1
    const wantLaser=!secondary || secondary.kind!=='laser'
    const usefulPickup=p=>(p.kind==='laser' && wantLaser) || (p.kind==='lightning' && !secondary && now<lightningUntil)
    function consider(item,reward,limit,isDrop=false){
      const d=dist(pos,item.pos)
      if (pausedUntil.has(item.id)) return
      if (d>limit || (!isDrop && Math.abs(item.pos[2]-pos[2])>7)) return
      if (dist(item.pos,zone.center)>zone.radius || (rush && dist(item.pos,zone.next_center)>zone.next_radius)) return
      if (zoneTimeRemaining(zone,item.pos,now)<d*1.6/speed+(isDrop?item.capture_s:1)+2) return
      const score=reward*(item.id===pursuit.id?1.12:1)/(8+d)
      if (score>value){value=score;goal=item.pos;selectedId=item.id;capture=isDrop && d<item.radius*.72}
    }
    if (!rush){
      const calm=!engaged || danger>90
      for (const c of obs.cubes) consider(c,110,calm?90:(danger<50?14:40))
      for (const {item:p,seen} of remembered.values())
        if (usefulPickup(p)) consider(p,(p.kind==='laser'?170:110)/(1+.04*(now-seen)),calm?150:(secondary?30:120))
      // farm crates hard while nobody is fighting us: every cube is +4% HP and damage
      if ((!engaged || danger>110) && hp>.4 && me.cubes<15)
        for (const [id,c] of crateMem){
          if (now-c.seen>90) continue
          const d=dist(pos,c.pos)
          // stand off ~25 m and shoot it rather than walk onto it
          const stand=d>28?[c.pos[0]+(pos[0]-c.pos[0])/d*24,c.pos[1]+(pos[1]-c.pos[1])/d*24,c.pos[2]]:pos
          const item={id,pos:d>28?stand:c.pos}
          if (Math.abs(c.pos[2]-pos[2])>7 && d<30) continue
          consider(item,85*(me.cubes<10?1:.6),180)
        }
      for (const drop of obs.drops){
        if (drop.lands_in<=dist(drop.pos,pos)/speed+2 && (drop.holder===null||drop.holder===me.id) && danger>35)
          consider(drop,hp<.75?420:150,130,true)
      }
    }

    // ---------- movement ----------
    let move=[0,0], boost=false, mode='roam'
    const lowHp = hp<.3 && obs.alive>2
    const losing = opponent && (opponent.hp??0)/(opponent.max_hp??1) > hp+.15
    if (engaged && !rush && lowHp && losing){
      mode='hide'
      if (!hide || now-hideAt>1.2){hide=findHide(pos,opponent,zone,now);hideAt=now}
      if (hide){goal=hide;selectedId=-1}
    } else hide=null

    const steerTo=(g)=>{
      const w=arena.steer(pos,g)
      let dx=w[0]-pos[0],dy=w[1]-pos[1]
      const L=Math.hypot(dx,dy)
      return L>1e-6?[dx/Math.max(1,L),dy/Math.max(1,L)]:[0,0]
    }
    const lootRun = engaged && mode!=='hide' && !rush && selectedId>=8000 && selectedId<9000 && !secondary
    if (lootRun){
      mode='loot'
      move=steerTo(goal)
      boost=me.grounded && stamina>(sprint?.25:.5); sprint=boost
    } else if (engaged && mode!=='hide' && !rush && opponent){
      mode='fight'
      const d=info.get(opponent.id).d
      const hasLaser=secondary?.kind==='laser', hasLightning=secondary?.kind==='lightning'
      const oppW=opponent.weapons??[]
      let pref=hasLightning && stamina>.3 ? (oppW.includes('laser')||oppW.includes('lightning') ? 30 : 60) : hasLaser ? 110 : 95
      if (!hasLightning && oppW.includes('lightning')) pref=Math.max(pref,75)
      if (hp<.5) pref+=25
      if (now>=strafeFlipAt){strafeDir=rand()<.5?-1:1;strafeFlipAt=now+.45+rand()*.9}
      const toE=[(opponent.pos[0]-pos[0])/Math.max(1,d),(opponent.pos[1]-pos[1])/Math.max(1,d)]
      const perp=[-toE[1]*strafeDir,toE[0]*strafeDir]
      const radial=clamp((d-pref)/25,-1,1)
      // no line of sight: close in (using the nav grid) so we can shoot
      if (!info.get(opponent.id).los && d>20){
        move=hp>.5||hasLightning?steerTo(opponent.pos):[perp[0],perp[1]]
      } else {
        let mv=[perp[0]+toE[0]*radial*1.2,perp[1]+toE[1]*radial*1.2]
        // several hunters: back away from the group so they all stay in our front arc
        const others=foes.filter(m=>m!==opponent && info.get(m.id).d<160)
        if (others.length){
          let rx=0,ry=0
          for (const m of [opponent,...others]){const dd=Math.max(15,info.get(m.id).d);rx+=(pos[0]-m.pos[0])/dd;ry+=(pos[1]-m.pos[1])/dd}
          const rl=Math.hypot(rx,ry)||1
          mv=[mv[0]*.6+rx/rl*1.1,mv[1]*.6+ry/rl*1.1]
        }
        let L=Math.hypot(mv[0],mv[1])||1; mv=[mv[0]/L,mv[1]/L]
        if (!freeAhead(pos,mv,10)){
          // pick the free direction closest to what we wanted (prefer flipping strafe)
          const want=Math.atan2(mv[1],mv[0])
          let best=null,bs=-Infinity
          for (let k=0;k<12;k++){
            const a=want+k*Math.PI/6, dv=[Math.cos(a),Math.sin(a)]
            if (!freeAhead(pos,dv,8)) continue
            const sc=Math.cos(a-want)+.3*Math.abs(dv[0]*perp[0]+dv[1]*perp[1])-.4*Math.max(0,dv[0]*toE[0]+dv[1]*toE[1])
            if (sc>bs){bs=sc;best=dv}
          }
          if (best){mv=best;if(best[0]*perp[0]+best[1]*perp[1]<0){strafeDir=-strafeDir;strafeFlipAt=now+.6+rand()*.6}}
          else mv=steerTo(zone.next_center)
        }
        // stay inside the zone while fighting
        if (dist(pos,zone.center)>zone.radius-15){const z=steerTo(zone.next_center);mv=[(mv[0]+z[0]*1.5),(mv[1]+z[1]*1.5)];L=Math.hypot(mv[0],mv[1])||1;mv=[mv[0]/L,mv[1]/L]}
        move=mv
      }
      boost=me.grounded && stamina>(sprint?.35:.6) && (!hasLightning || stamina>.5)
      sprint=boost
    } else {
      const remaining=dist(pos,goal)
      if (selectedId!==pursuit.id || remaining<pursuit.best-2 || capture) pursuit={id:selectedId,best:remaining,progressAt:now}
      else if (selectedId>=0 && now-pursuit.progressAt>6) pausedUntil.set(selectedId,now+12)
      const resting=!rush && hp<.7 && selectedId<0 && now-lastHurt<25 && left>25
      const moving=!capture && remaining>1.5 && !resting
      if (moving) move=steerTo(goal)
      sprint=(rush||mode==='hide'||danger<90) && stamina>(sprint?.3:.7)
      boost=moving && sprint && me.grounded
    }
    // stay in a supply ring we are about to finish capturing (3 cubes + full HP + full energy)
    if (canFinishCapture(obs, C, lastHurt)) { move=[0,0]; boost=sprint=false }
    {
      const g=C.weapons.mortar.gravity??22, R=(C.weapons.mortar.splash_radius??7)+4
      let threat=null
      for (const p of obs.projectiles){
        if (p.kind!=='mortar') continue
        let x=p.pos[0],y=p.pos[1],z=p.pos[2],vx=p.vel[0],vy=p.vel[1],vz=p.vel[2]
        for (let k=0;k<80;k++){
          x+=vx*.05;y+=vy*.05;vz-=g*.05;z+=vz*.05
          if (vz<0 && z<=world.ground(x,y)+.3) break
          if (world.solidAt(x,y,z)) break
        }
        const tl=Math.hypot(x-p.pos[0],y-p.pos[1])/Math.max(1,Math.hypot(p.vel[0],p.vel[1]))
        if (dist([x,y],pos)<R && tl<3 && (!threat||tl<threat.tl)) threat={x,y,tl}
      }
      if (threat){
        let ax=pos[0]-threat.x, ay=pos[1]-threat.y
        const L=Math.hypot(ax,ay)
        if (L<.5){ax=-move[1]||1;ay=move[0]||0}
        let dv=[ax/(Math.hypot(ax,ay)||1),ay/(Math.hypot(ax,ay)||1)]
        if (!freeAhead(pos,dv,6)) dv=[-dv[1],dv[0]]
        move=dv; boost=me.grounded && me.energy>8
      }
    }
    const moving=Math.hypot(move[0],move[1])>.1
    if (now-sampleTime>=.8){
      if (previousMove && moving && lastPos && dist(lastPos,pos)<1.5) stuckUntil=now+.85
      lastPos=pos;sampleTime=now
    }
    previousMove=moving
    const stuck=moving && now<stuckUntil
    if (stuck){ boost=stamina>.12 }

    const act={tick:obs.tick,move,boost,jump:stuck && me.grounded,
      aim:[me.aim[0],0],fire:[false,false],reload:false}

    // ---------- aim + fire ----------
    const M=[pos[0]+Math.cos(me.aim[0])*3,pos[1]+Math.sin(me.aim[0])*3,muzzleZ]
    if (target){
      const d=tInfo.d
      const laser=secondary?.kind==='laser'?secondary:null
      const charge=laser?.charge??0
      const laserFull=laser && charge>=laser.stats.charge-.03
      const gunAim=gun?leadAim(M,target,gun.stats.speed):null
      const beamAim=leadAim(M,target,1e6,.06)
      const useBeam=laserFull && target===opponent
      const a=useBeam||!gunAim?beamAim:gunAim
      act.aim=[a.yaw,a.pitch]
      const r=(target.kind==='npc'?1.6:2.3*(1+(target.cubes??0)*sizePer))
      const tol=Math.atan2(r,Math.max(8,d))
      const yawErr=Math.abs(wrap(a.yaw-me.aim[0]))
      const pitchErr=Math.abs(a.pitch-me.aim[1])
      const aligned=yawErr<tol+.01 && pitchErr<Math.atan2(4,Math.max(8,d))+.02
      const clear=tInfo.los
      const holdFire=false
      if (gun && clear && !holdFire && d<gun.stats.range*.98 && aligned && gun.reload<=0)
        act.fire[gunSlot]=true
      if (secondary?.kind==='lightning' && opponent){
        const lr=secondary.stats.range, cone=(C.weapons.lightning.cone_deg??30)*Math.PI/360
        const ok=foes.concat(npcs).some(m=>{const i=info.get(m.id);return i.d<lr*.97 && i.los && Math.abs(wrap(Math.atan2(m.pos[1]-pos[1],m.pos[0]-pos[0])-me.aim[0]))<cone*.9})
        if (ok && me.energy>=(secondary.stats.energy??3)+4) act.fire[secSlot]=true
      }
      if (laser && target===opponent){
        const canCharge=laser.cooldown<=.05 && d<laser.stats.range && mode!=='hide'
        if (charge>0){
          if (laserHeldSince<0) laserHeldSince=now
          const beamYawErr=Math.abs(wrap(beamAim.yaw-me.aim[0]))
          const beamOk=beamYawErr<tol*.9+.004 && Math.abs(beamAim.pitch-me.aim[1])<Math.atan2(3.5,Math.max(8,d))
          const timeout=now-laserHeldSince>(C.weapons.laser.charge+C.weapons.laser.hold_s-.25)
          // keep holding until full and on target; release to fire
          act.fire[secSlot]=!( (laserFull && beamOk && clear) || timeout )
          if (!clear && laserFull && !timeout) act.fire[secSlot]=true
        } else {
          laserHeldSince=-1
          act.fire[secSlot]=canCharge && (clear || d<60)
        }
      } else if (laser && charge>0) {
        // target lost: release only if something is in front, else keep holding briefly
        act.fire[secSlot]=now-laserHeldSince<1.5
      }
      if (!laser) laserHeldSince=-1
    } else {
      laserHeldSince=-1
      // face the most recent threat, else scan
      let yaw
      if (now-lastHurt<3) yaw=hurtBearing
      else if (now-alertT<2.5) yaw=alertBearing
      else if (closest) yaw=Math.atan2(closest.pos[1]-pos[1],closest.pos[0]-pos[0])
      else yaw=me.aim[0]+.9
      act.aim=[yaw,0]
      // shoot visible crates in range (gun, plus a full laser shot)
      const crate=obs.crates.filter(c=>dist(pos,c.pos)<70 && Math.abs(c.pos[2]-pos[2])<20)
        .sort((a,b)=>dist(pos,a.pos)-dist(pos,b.pos))[0]
      if (crate && !rush && now-lastHurt>1.5){
        const cc=[crate.pos[0],crate.pos[1],crate.pos[2]+1.2]
        const dd=Math.max(1,dist(pos,crate.pos))
        const yaw=Math.atan2(cc[1]-M[1],cc[0]-M[0]), pitch=Math.atan2(cc[2]-muzzleZ,dd)
        act.aim=[yaw,pitch]
        const yawErr=Math.abs(wrap(yaw-me.aim[0])), pErr=Math.abs(pitch-me.aim[1])
        const ok=yawErr<Math.atan2(1.2,dd)+.01 && pErr<Math.atan2(1.2,dd)+.02 && world.los([pos[0],pos[1],muzzleZ],cc,3.2)
        if (ok && gun && gun.reload<=0) act.fire[gunSlot]=true
        const lz=secondary?.kind==='laser'?secondary:null
        if (lz && dd<lz.stats.range){
          const full=(lz.charge??0)>=lz.stats.charge-.03
          if ((lz.charge??0)>0) act.fire[secSlot]=!(full && ok)
          else act.fire[secSlot]=lz.cooldown<=.05 && (crate.hp??300)>120
        }
      }
    }
    if (gun && !act.fire[gunSlot] && gun.reload<=0 && gun.ammo<gun.stats.mag*.5 && !(tInfo&&tInfo.los)) act.reload=true
    if (gun && gun.ammo===0 && gun.reload<=0) act.reload=true

    // ---------- loot ----------
    if (wantLaser){
      const pick=obs.pickups.filter(p=>usefulPickup(p) && dist(pos,p.pos)<=C.loot.pickup_radius-.2 && Math.abs(pos[2]-p.pos[2])<=4)
        .sort((a,b)=>Number(b.kind==='laser')-Number(a.kind==='laser'))[0]
      if (pick){
        act.take=pick.id
        if (secondary && secondary.kind!=='laser' && !(secondary.charge>0)) act.drop=secSlot
      }
    }
    if (obs.offer){
      const base=['armor','plating','nanites']
      const kit=secondary?.kind==='laser'?['armor','laser_damage','plating','nanites','laser_charge','gun_damage','gun_rate','gyros']
        :secondary?.kind==='lightning'?['armor','lightning_efficiency','lightning_range','plating','nanites','gun_damage','servos']
        :['armor','gun_damage','plating','gun_rate','nanites','gun_velocity','gun_mag']
      const order=[...kit,...base,'servos','reactor','capacitor','gyros','sensors']
      const rank=c=>{const i=order.indexOf(c.id);return i<0?99:i}
      act.pick=obs.offer.choices.reduce((best,c,i,list)=>rank(c)<rank(list[best])?i:best,0)
    }
    return act
  }}
})
