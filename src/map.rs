#![allow(dead_code)]
use serde::Deserialize;
use std::cell::RefCell;
use std::collections::BinaryHeap;
use std::cmp::Ordering;
use std::fs;
use std::path::Path;

#[derive(Debug, Clone, Deserialize)]
struct RawWorld {
    bounds: RawBounds,
    terrain: RawTerrain,
    #[serde(default)]
    rules: RawRules,
    #[serde(default)]
    prisms: Vec<RawPrism>,
    #[serde(default)]
    trees: RawTrees,
}

#[derive(Debug, Clone, Deserialize)]
struct RawBounds {
    #[serde(rename = "minX")]
    min_x: f64,
    #[serde(rename = "maxX")]
    max_x: f64,
    #[serde(rename = "minY")]
    min_y: f64,
    #[serde(rename = "maxY")]
    max_y: f64,
}

#[derive(Debug, Clone, Deserialize)]
struct RawTerrain {
    #[serde(rename = "minX")]
    min_x: f64,
    #[serde(rename = "minY")]
    min_y: f64,
    step: f64,
    nx: usize,
    ny: usize,
    #[serde(default = "default_scale")]
    scale: f64,
}

fn default_scale() -> f64 { 0.01 }

#[derive(Debug, Clone, Default, Deserialize)]
struct RawRules {
    #[serde(rename = "canopyDensity", default = "default_canopy_density")]
    canopy_density: f64,
    #[serde(rename = "canopyBlockT", default = "default_canopy_block_t")]
    canopy_block_t: f64,
}

fn default_canopy_density() -> f64 { 0.35 }
fn default_canopy_block_t() -> f64 { 0.25 }

#[derive(Debug, Clone, Deserialize)]
struct RawPrism {
    #[serde(default)]
    kind: String,
    base: f64,
    top: f64,
    ring: Vec<f64>,
    #[serde(default)]
    holes: Vec<Vec<f64>>,
}

#[derive(Debug, Clone, Default, Deserialize)]
struct RawTrees {
    #[serde(default)]
    fields: Vec<String>,
    #[serde(default)]
    data: Vec<f64>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PrismKind {
    Building,
    Stadium,
    Bridge,
}

#[derive(Debug, Clone)]
pub struct Prism {
    pub id: usize,
    pub kind: PrismKind,
    pub base: f64,
    pub top: f64,
    pub min_x: f64,
    pub max_x: f64,
    pub min_y: f64,
    pub max_y: f64,
    pub outer: Vec<[f64; 2]>,
    pub holes: Vec<Vec<[f64; 2]>>,
    pub centroid: [f64; 2],
    pub area: f64,
}

#[derive(Debug, Clone, Copy)]
pub struct Tree {
    pub x: f64,
    pub y: f64,
    pub z: f64,
    pub height: f64,
    pub crown_r: f64,
    pub crown_base: f64,
    pub trunk_r: f64,
    pub crown_x: f64,
    pub crown_y: f64,
}

#[derive(Debug, Clone, Copy)]
pub struct RoofEdgePoint {
    pub pos: [f64; 3],         // Inset ~3.2m from outer rim (safe standing center on roof)
    pub outward_yaw: f64,      // Outward normal of this roof edge
    pub launch_xy: [f64; 2],   // Ground/podium launch point ~4.5m outside the wall
    pub drop_z: f64,           // Highest walkable surface z below roof at launch_xy
    pub launch_clear: bool,    // True if a mech cylinder fits cleanly at launch_xy/drop_z
}

#[derive(Debug, Clone, Copy)]
pub struct RoofHop {
    pub to_roof_idx: usize,
    pub from_pos: [f64; 3],
    pub to_pos: [f64; 3],
    pub horiz_dist: f64,
    pub dz: f64,
    pub min_energy: f64,
}

#[derive(Debug, Clone)]
pub struct Rooftop {
    pub prism_id: usize,
    pub pos: [f64; 3],                // Pole-of-inaccessibility deepest interior point
    pub height_above_ground: f64,     // 6..=78m
    pub area: f64,
    pub has_penthouse_cover: bool,
    pub interior_pts: Vec<[f64; 3]>,  // 3..6 recessed interior & penthouse-shadow cover points
    pub edge_pts: Vec<RoofEdgePoint>, // 6..12 rim peek & ground-climb entry points
    pub hops: Vec<RoofHop>,           // Top 6 reachable adjacent roofs
}

#[derive(Debug, Clone, Copy)]
pub struct RayHit {
    pub t: f64,
    pub pos: [f64; 3],
    pub prism_id: Option<usize>, // None = terrain
}

pub const NAV_STEP: f64 = 8.0;
const GRID_CELL: f64 = 16.0;

struct AStarScratch {
    gen: u32,
    seen_gen: Vec<u32>,
    g_score: Vec<f32>,
    came_from: Vec<u32>,
    heap: BinaryHeap<AStarNode>,
    path: Vec<usize>,
}

impl AStarScratch {
    fn new(n: usize) -> Self {
        Self {
            gen: 0,
            seen_gen: vec![0; n],
            g_score: vec![f32::INFINITY; n],
            came_from: vec![u32::MAX; n],
            heap: BinaryHeap::with_capacity(512),
            path: Vec::with_capacity(128),
        }
    }
    fn reset(&mut self, n: usize) -> u32 {
        if self.seen_gen.len() != n {
            self.seen_gen.resize(n, 0);
            self.g_score.resize(n, f32::INFINITY);
            self.came_from.resize(n, u32::MAX);
            self.gen = 0;
        }
        self.gen = self.gen.wrapping_add(1);
        if self.gen == 0 {
            self.seen_gen.fill(0);
            self.gen = 1;
        }
        self.heap.clear();
        self.path.clear();
        self.gen
    }
}

pub struct ArenaMap {
    pub min_x: f64,
    pub max_x: f64,
    pub min_y: f64,
    pub max_y: f64,
    // Terrain
    t_min_x: f64,
    t_min_y: f64,
    t_step: f64,
    t_nx: usize,
    t_ny: usize,
    t_scale: f64,
    max_terrain_z: f64,
    terrain: Vec<i16>,
    // Prisms & spatial grid
    pub prisms: Vec<Prism>,
    grid_min_x: f64,
    grid_min_y: f64,
    grid_nx: usize,
    grid_ny: usize,
    prism_grid: Vec<Vec<u16>>,
    // Trees & spatial grid
    pub trees: Vec<Tree>,
    tree_grid: Vec<Vec<u16>>,
    pub canopy_density: f64,
    pub canopy_block_t: f64,
    // Tactical rooftops + O(1) prism->roof lookup
    pub rooftops: Vec<Rooftop>,
    pub prism_to_roof: Vec<Option<u16>>,
    // Coarse 2D navigation grid (ground walkability, headroom for cube-growth bridge clearance, elevation, cover)
    pub nav_nx: usize,
    pub nav_ny: usize,
    pub nav_min_x: f64,
    pub nav_min_y: f64,
    pub nav_walkable: Vec<bool>,
    pub nav_headroom: Vec<f32>,
    pub nav_z: Vec<f64>,
    pub nav_cover: Vec<f32>,
    astar_scratch: RefCell<AStarScratch>,
}

impl ArenaMap {
    pub fn load(map_dir: &str) -> Result<Self, String> {
        let dir = Path::new(map_dir);
        let world_path = dir.join("v2").join("world.json");
        let terrain_path = dir.join("v2").join("terrain.i16");

        let world_str = fs::read_to_string(&world_path)
            .map_err(|e| format!("failed reading {}: {}", world_path.display(), e))?;
        let raw: RawWorld = serde_json::from_str(&world_str)
            .map_err(|e| format!("failed parsing world.json: {}", e))?;

        let raw_bytes = fs::read(&terrain_path)
            .map_err(|e| format!("failed reading {}: {}", terrain_path.display(), e))?;
        let expected_samples = raw.terrain.nx * raw.terrain.ny;
        if raw_bytes.len() < expected_samples * 2 {
            return Err(format!(
                "terrain.i16 too small: {} < {}",
                raw_bytes.len(),
                expected_samples * 2
            ));
        }
        let mut terrain = Vec::with_capacity(expected_samples);
        let mut max_terrain_z = f64::NEG_INFINITY;
        for chunk in raw_bytes[..expected_samples * 2].chunks_exact(2) {
            let v = i16::from_le_bytes([chunk[0], chunk[1]]);
            let z = v as f64 * raw.terrain.scale;
            if z > max_terrain_z { max_terrain_z = z; }
            terrain.push(v);
        }
        if !max_terrain_z.is_finite() { max_terrain_z = 0.0; }

        // Parse prisms
        let mut prisms = Vec::with_capacity(raw.prisms.len());
        let mut p_min_x = raw.bounds.min_x;
        let mut p_max_x = raw.bounds.max_x;
        let mut p_min_y = raw.bounds.min_y;
        let mut p_max_y = raw.bounds.max_y;

        for (id, rp) in raw.prisms.into_iter().enumerate() {
            let outer = flat_to_ring(&rp.ring);
            if outer.len() < 3 {
                continue;
            }
            let mut holes = Vec::with_capacity(rp.holes.len());
            for h in rp.holes {
                let hr = flat_to_ring(&h);
                if hr.len() >= 3 {
                    holes.push(hr);
                }
            }
            let mut min_x = f64::INFINITY;
            let mut max_x = f64::NEG_INFINITY;
            let mut min_y = f64::INFINITY;
            let mut max_y = f64::NEG_INFINITY;
            for &[x, y] in &outer {
                if x < min_x { min_x = x; }
                if x > max_x { max_x = x; }
                if y < min_y { min_y = y; }
                if y > max_y { max_y = y; }
            }
            p_min_x = p_min_x.min(min_x);
            p_max_x = p_max_x.max(max_x);
            p_min_y = p_min_y.min(min_y);
            p_max_y = p_max_y.max(max_y);

            let (area, centroid) = polygon_area_centroid(&outer);
            let kind = match rp.kind.as_str() {
                "bridge" => PrismKind::Bridge,
                "stadium" => PrismKind::Stadium,
                _ => PrismKind::Building,
            };
            prisms.push(Prism {
                id,
                kind,
                base: rp.base,
                top: rp.top,
                min_x,
                max_x,
                min_y,
                max_y,
                outer,
                holes,
                centroid,
                area,
            });
        }

        // Spatial grid for prisms and trees
        let grid_min_x = p_min_x - 32.0;
        let grid_min_y = p_min_y - 32.0;
        let grid_nx = (((p_max_x + 32.0) - grid_min_x) / GRID_CELL).ceil() as usize + 1;
        let grid_ny = (((p_max_y + 32.0) - grid_min_y) / GRID_CELL).ceil() as usize + 1;
        let mut prism_grid = vec![Vec::<u16>::new(); grid_nx * grid_ny];
        for (idx, p) in prisms.iter().enumerate() {
            let ix0 = (((p.min_x - 4.0) - grid_min_x) / GRID_CELL).floor().max(0.0) as usize;
            let ix1 = ((((p.max_x + 4.0) - grid_min_x) / GRID_CELL).floor().max(0.0) as usize).min(grid_nx - 1);
            let iy0 = (((p.min_y - 4.0) - grid_min_y) / GRID_CELL).floor().max(0.0) as usize;
            let iy1 = ((((p.max_y + 4.0) - grid_min_y) / GRID_CELL).floor().max(0.0) as usize).min(grid_ny - 1);
            for iy in iy0..=iy1 {
                for ix in ix0..=ix1 {
                    prism_grid[iy * grid_nx + ix].push(idx as u16);
                }
            }
        }

        // Parse trees
        let mut trees = Vec::new();
        let mut tree_grid = vec![Vec::<u16>::new(); grid_nx * grid_ny];
        let nf = raw.trees.fields.len();
        if nf > 0 && raw.trees.data.len() >= nf {
            let f_idx = |name: &str| raw.trees.fields.iter().position(|s| s == name);
            let ix_x = f_idx("x");
            let ix_y = f_idx("y");
            let ix_z = f_idx("z");
            let ix_h = f_idx("height");
            let ix_cr = f_idx("crownR");
            let ix_cb = f_idx("crownBase");
            let ix_tr = f_idx("trunkR");
            let ix_cx = f_idx("crownX");
            let ix_cy = f_idx("crownY");
            if let (Some(fx), Some(fy), Some(fz), Some(fh)) = (ix_x, ix_y, ix_z, ix_h) {
                let count = raw.trees.data.len() / nf;
                trees.reserve(count);
                for i in 0..count {
                    let row = &raw.trees.data[i * nf..(i + 1) * nf];
                    let x = row[fx];
                    let y = row[fy];
                    let z = row[fz];
                    let height = row[fh];
                    let crown_r = ix_cr.map(|k| row[k]).unwrap_or(3.0);
                    let crown_base = ix_cb.map(|k| row[k]).unwrap_or(z + height * 0.4);
                    let trunk_r = ix_tr.map(|k| row[k]).unwrap_or(0.5);
                    let crown_x = ix_cx.map(|k| row[k]).unwrap_or(x);
                    let crown_y = ix_cy.map(|k| row[k]).unwrap_or(y);
                    let tid = trees.len() as u16;
                    trees.push(Tree {
                        x, y, z, height, crown_r, crown_base, trunk_r, crown_x, crown_y,
                    });
                    let r = crown_r.max(trunk_r) + 1.0;
                    let gx0 = (((x.min(crown_x) - r) - grid_min_x) / GRID_CELL).floor().max(0.0) as usize;
                    let gx1 = ((((x.max(crown_x) + r) - grid_min_x) / GRID_CELL).floor().max(0.0) as usize).min(grid_nx - 1);
                    let gy0 = (((y.min(crown_y) - r) - grid_min_y) / GRID_CELL).floor().max(0.0) as usize;
                    let gy1 = ((((y.max(crown_y) + r) - grid_min_y) / GRID_CELL).floor().max(0.0) as usize).min(grid_ny - 1);
                    for gy in gy0..=gy1 {
                        for gx in gx0..=gx1 {
                            tree_grid[gy * grid_nx + gx].push(tid);
                        }
                    }
                }
            }
        }

        let mut map = ArenaMap {
            min_x: raw.bounds.min_x,
            max_x: raw.bounds.max_x,
            min_y: raw.bounds.min_y,
            max_y: raw.bounds.max_y,
            t_min_x: raw.terrain.min_x,
            t_min_y: raw.terrain.min_y,
            t_step: raw.terrain.step,
            t_nx: raw.terrain.nx,
            t_ny: raw.terrain.ny,
            t_scale: raw.terrain.scale,
            max_terrain_z,
            terrain,
            prisms,
            grid_min_x,
            grid_min_y,
            grid_nx,
            grid_ny,
            prism_grid,
            trees,
            tree_grid,
            canopy_density: raw.rules.canopy_density,
            canopy_block_t: raw.rules.canopy_block_t,
            rooftops: Vec::new(),
            prism_to_roof: Vec::new(),
            nav_nx: 0,
            nav_ny: 0,
            nav_min_x: raw.bounds.min_x + 4.0,
            nav_min_y: raw.bounds.min_y + 4.0,
            nav_walkable: Vec::new(),
            nav_headroom: Vec::new(),
            nav_z: Vec::new(),
            nav_cover: Vec::new(),
            astar_scratch: RefCell::new(AStarScratch::new(0)),
        };

        map.build_rooftops_and_nav();
        Ok(map)
    }

    pub fn fallback_empty() -> Self {
        ArenaMap {
            min_x: -512.0,
            max_x: 555.0,
            min_y: -333.0,
            max_y: 388.0,
            t_min_x: -513.0,
            t_min_y: -334.0,
            t_step: 1.0,
            t_nx: 2,
            t_ny: 2,
            t_scale: 0.01,
            max_terrain_z: 0.0,
            terrain: vec![0; 4],
            prisms: Vec::new(),
            grid_min_x: -550.0,
            grid_min_y: -370.0,
            grid_nx: 1,
            grid_ny: 1,
            prism_grid: vec![Vec::new()],
            trees: Vec::new(),
            tree_grid: vec![Vec::new()],
            canopy_density: 0.35,
            canopy_block_t: 0.25,
            rooftops: Vec::new(),
            prism_to_roof: Vec::new(),
            nav_nx: 1,
            nav_ny: 1,
            nav_min_x: 0.0,
            nav_min_y: 0.0,
            nav_walkable: vec![true],
            nav_headroom: vec![999.0],
            nav_z: vec![0.0],
            nav_cover: vec![0.0],
            astar_scratch: RefCell::new(AStarScratch::new(1)),
        }
    }

    fn build_rooftops_and_nav(&mut self) {
        self.prism_to_roof = vec![None; self.prisms.len()];

        // 1. Identify climbable tactical rooftops with interior/penthouse cover & perimeter edge-peek points!
        // Exclude PrismKind::Stadium (jagged multi-tier bowl/bleacher trap!)
        for idx in 0..self.prisms.len() {
            let (p_kind, p_area, p_top, p_min_x, p_max_x, p_min_y, p_max_y) = {
                let p = &self.prisms[idx];
                (p.kind, p.area, p.top, p.min_x, p.max_x, p.min_y, p.max_y)
            };
            if p_area < 45.0 || p_kind == PrismKind::Stadium {
                continue;
            }

            // Sample up to 9x9 grid inside AABB to find true pole-of-inaccessibility + recessed interior points
            let max_dim = (p_max_x - p_min_x).max(p_max_y - p_min_y).max(1.0);
            let step = (max_dim / 9.0).clamp(3.5, 9.0);
            let nx = ((p_max_x - p_min_x) / step).ceil().clamp(2.0, 9.0) as usize;
            let ny = ((p_max_y - p_min_y) / step).ceil().clamp(2.0, 9.0) as usize;

            let mut valid_pts: Vec<(f64, [f64; 3])> = Vec::new();
            for gy in 1..ny {
                let y = p_min_y + (p_max_y - p_min_y) * (gy as f64 / ny as f64);
                for gx in 1..nx {
                    let x = p_min_x + (p_max_x - p_min_x) * (gx as f64 / nx as f64);
                    if !self.point_in_prism_2d(idx, x, y) {
                        continue;
                    }
                    if self.is_cylinder_blocked(x, y, p_top + 0.05, 2.7, 9.5, 0.5) {
                        continue;
                    }
                    let d_outer = dist_sq_to_ring(x, y, &self.prisms[idx].outer).sqrt();
                    if d_outer >= 2.0 {
                        valid_pts.push((d_outer, [x, y, p_top]));
                    }
                }
            }
            if valid_pts.is_empty() {
                let c = self.prisms[idx].centroid;
                if self.point_in_prism_2d(idx, c[0], c[1])
                    && !self.is_cylinder_blocked(c[0], c[1], p_top + 0.05, 2.7, 9.5, 0.5)
                {
                    let d_outer = dist_sq_to_ring(c[0], c[1], &self.prisms[idx].outer).sqrt();
                    valid_pts.push((d_outer, [c[0], c[1], p_top]));
                }
            }
            if valid_pts.is_empty() {
                continue;
            }
            valid_pts.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
            let pole = valid_pts[0].1;
            let tz = self.terrain_z(pole[0], pole[1]);
            let h_ag = p_top - tz;
            if !(6.0..=78.0).contains(&h_ag) {
                continue;
            }

            // Detect adjacent/embedded taller penthouse prisms for 360-deg rooftop cover!
            let mut has_penthouse_cover = false;
            let mut interior_pts: Vec<[f64; 3]> = Vec::new();
            for q_idx in 0..self.prisms.len() {
                if q_idx == idx { continue; }
                let q = &self.prisms[q_idx];
                if q.top < p_top + 4.5 || q.base > p_top + 2.0 { continue; }
                if q.max_x < p_min_x - 8.0 || q.min_x > p_max_x + 8.0 || q.max_y < p_min_y - 8.0 || q.min_y > p_max_y + 8.0 {
                    continue;
                }
                has_penthouse_cover = true;
                let qx = q.centroid[0];
                let qy = q.centroid[1];
                let rx = (q.max_x - q.min_x) * 0.5 + 4.5;
                let ry = (q.max_y - q.min_y) * 0.5 + 4.5;
                for &(ox, oy) in &[(rx, 0.0), (-rx, 0.0), (0.0, ry), (0.0, -ry)] {
                    let sx = qx + ox;
                    let sy = qy + oy;
                    if self.point_in_prism_2d(idx, sx, sy)
                        && !self.is_cylinder_blocked(sx, sy, p_top + 0.05, 2.8, 9.5, 0.5)
                    {
                        if interior_pts.iter().all(|a| (a[0] - sx).hypot(a[1] - sy) >= 6.0) {
                            interior_pts.push([sx, sy, p_top]);
                        }
                    }
                }
                if interior_pts.len() >= 4 { break; }
            }
            // Fill remaining interior points (up to 6) with deepest well-spaced spots
            for &(_, pt) in &valid_pts {
                if interior_pts.len() >= 6 { break; }
                if interior_pts.iter().all(|a| (a[0] - pt[0]).hypot(a[1] - pt[1]) >= 7.0) {
                    interior_pts.push(pt);
                }
            }
            if interior_pts.is_empty() {
                interior_pts.push(pole);
            }

            // Generate 6..12 perimeter edge-peek & ground-climb launch points along outer ring!
            let mut edge_pts: Vec<RoofEdgePoint> = Vec::new();
            let ring = self.prisms[idx].outer.clone();
            let n_ring = ring.len();
            let mut acc_len = 0.0;
            let spacing = (max_dim * 0.35).clamp(11.0, 18.0);
            let mut next_sample = 4.0;
            for i in 0..n_ring {
                if edge_pts.len() >= 12 { break; }
                let a = ring[i];
                let b = ring[(i + 1) % n_ring];
                let ex = b[0] - a[0];
                let ey = b[1] - a[1];
                let seg_len = ex.hypot(ey);
                if seg_len < 1.5 { continue; }
                let ux = ex / seg_len;
                let uy = ey / seg_len;
                // Test both perpendicular normals to identify inward vs outward (winding-agnostic!)
                let n1 = [-uy, ux];
                let n2 = [uy, -ux];

                let mut pos_on_seg = next_sample - acc_len;
                while pos_on_seg <= seg_len && edge_pts.len() < 12 {
                    let bx = a[0] + ux * pos_on_seg;
                    let by = a[1] + uy * pos_on_seg;
                    let in_n = if self.point_in_prism_2d(idx, bx + n1[0] * 1.5, by + n1[1] * 1.5) {
                        n1
                    } else if self.point_in_prism_2d(idx, bx + n2[0] * 1.5, by + n2[1] * 1.5) {
                        n2
                    } else {
                        pos_on_seg += spacing;
                        continue;
                    };
                    let out_n = [-in_n[0], -in_n[1]];
                    let ix = bx + in_n[0] * 3.2;
                    let iy = by + in_n[1] * 3.2;
                    if self.point_in_prism_2d(idx, ix, iy)
                        && !self.is_cylinder_blocked(ix, iy, p_top + 0.05, 2.7, 9.5, 0.5)
                        && edge_pts.iter().all(|ep| (ep.pos[0] - ix).hypot(ep.pos[1] - iy) >= 9.0)
                    {
                        let out_yaw = out_n[1].atan2(out_n[0]);
                        let lx = (bx + out_n[0] * 4.8).clamp(self.min_x + 6.0, self.max_x - 6.0);
                        let ly = (by + out_n[1] * 4.8).clamp(self.min_y + 6.0, self.max_y - 6.0);
                        // Compute highest lower surface (supports Morrill/Lincoln Tower 9.4m podiums!)
                        let mut drop_z = self.terrain_z(lx, ly);
                        if let Some((gx, gy)) = self.grid_coords(lx, ly) {
                            for &qid in &self.prism_grid[gy * self.grid_nx + gx] {
                                let q_idx = qid as usize;
                                if q_idx == idx { continue; }
                                let q = &self.prisms[q_idx];
                                if q.top < p_top - 1.5 && q.top > drop_z && self.point_in_prism_2d(q_idx, lx, ly) {
                                    drop_z = q.top;
                                }
                            }
                        }
                        let launch_clear = !self.is_cylinder_blocked(lx, ly, drop_z + 0.05, 2.7, 9.5, 1.2)
                            && !self.is_covered_above(lx, ly, drop_z + 1.0, (p_top - drop_z).max(4.0) + 6.0);
                        // Ensure outward sightline isn't immediately buried inside an adjacent taller wall
                        let out_eye = [ix, iy, p_top + 6.0];
                        let out_chk = [ix + out_n[0] * 8.0, iy + out_n[1] * 8.0, p_top + 6.0];
                        if self.has_shot_los(out_eye, out_chk) {
                            edge_pts.push(RoofEdgePoint {
                                pos: [ix, iy, p_top],
                                outward_yaw: out_yaw,
                                launch_xy: [lx, ly],
                                drop_z,
                                launch_clear,
                            });
                        }
                    }
                    pos_on_seg += spacing;
                }
                acc_len += seg_len;
                while next_sample <= acc_len {
                    next_sample += spacing;
                }
            }
            // Ensure at least 1 edge point exists
            if edge_pts.is_empty() {
                let lx = (pole[0] + 5.0).clamp(self.min_x + 6.0, self.max_x - 6.0);
                let ly = pole[1].clamp(self.min_y + 6.0, self.max_y - 6.0);
                let dz = self.terrain_z(lx, ly);
                edge_pts.push(RoofEdgePoint {
                    pos: pole,
                    outward_yaw: 0.0,
                    launch_xy: [lx, ly],
                    drop_z: dz,
                    launch_clear: !self.is_cylinder_blocked(lx, ly, dz + 0.05, 2.7, 9.5, 1.2),
                });
            }

            let roof_idx = self.rooftops.len();
            self.prism_to_roof[idx] = Some(roof_idx as u16);
            self.rooftops.push(Rooftop {
                prism_id: idx,
                pos: pole,
                height_above_ground: h_ag,
                area: p_area,
                has_penthouse_cover,
                interior_pts,
                edge_pts,
                hops: Vec::new(),
            });
        }

        // 1b. Precompute Roof-to-Roof Hop Graph (adjacent roofs within 22m air gap!)
        let nr = self.rooftops.len();
        for a in 0..nr {
            let pa = &self.prisms[self.rooftops[a].prism_id];
            let mut hops: Vec<RoofHop> = Vec::new();
            for b in 0..nr {
                if a == b { continue; }
                let pb = &self.prisms[self.rooftops[b].prism_id];
                // Quick AABB distance reject (> 26m)
                let dx_bb = (pa.min_x - pb.max_x).max(pb.min_x - pa.max_x).max(0.0);
                let dy_bb = (pa.min_y - pb.max_y).max(pb.min_y - pa.max_y).max(0.0);
                if dx_bb.hypot(dy_bb) > 26.0 { continue; }
                let dz = pb.top - pa.top;
                if dz < -32.0 || dz > 26.0 { continue; }

                // Closest (edge_a, edge_b) pair
                let mut best_pair: Option<(f64, [f64; 3], [f64; 3])> = None;
                for ea in &self.rooftops[a].edge_pts {
                    for eb in &self.rooftops[b].edge_pts {
                        let d = (ea.pos[0] - eb.pos[0]).hypot(ea.pos[1] - eb.pos[1]);
                        if best_pair.map(|(bd, _, _)| d < bd).unwrap_or(true) {
                            best_pair = Some((d, ea.pos, eb.pos));
                        }
                    }
                }
                if let Some((hd, from_p, to_p)) = best_pair {
                    let air_gap = (hd - 6.4).max(0.0);
                    if air_gap > 22.0 { continue; }
                    let min_energy = if dz <= 0.5 {
                        if air_gap <= 9.5 { 10.0 } else { 10.0 + (air_gap - 9.5) * 1.7 }
                    } else {
                        ((dz / 8.8).max(air_gap / 13.0)) * 16.5 + 10.0
                    };
                    if min_energy > 75.0 { continue; }
                    let mid_z = from_p[2].max(to_p[2]) + 4.0;
                    if !self.has_shot_los([from_p[0], from_p[1], mid_z], [to_p[0], to_p[1], mid_z]) {
                        continue;
                    }
                    hops.push(RoofHop {
                        to_roof_idx: b,
                        from_pos: from_p,
                        to_pos: to_p,
                        horiz_dist: hd,
                        dz,
                        min_energy,
                    });
                }
            }
            hops.sort_by(|x, y| x.min_energy.partial_cmp(&y.min_energy).unwrap_or(std::cmp::Ordering::Equal));
            hops.truncate(6);
            self.rooftops[a].hops = hops;
        }

        // 2. Build 8m ground navigation grid with dynamic headroom (for cube-grown bridge clearance!)
        self.nav_min_x = self.min_x + 4.0;
        self.nav_min_y = self.min_y + 4.0;
        self.nav_nx = (((self.max_x - 4.0) - self.nav_min_x) / NAV_STEP).floor().max(1.0) as usize + 1;
        self.nav_ny = (((self.max_y - 4.0) - self.nav_min_y) / NAV_STEP).floor().max(1.0) as usize + 1;
        let n = self.nav_nx * self.nav_ny;
        self.nav_walkable = vec![false; n];
        self.nav_headroom = vec![999.0; n];
        self.nav_z = vec![0.0; n];
        self.nav_cover = vec![0.0; n];
        *self.astar_scratch.borrow_mut() = AStarScratch::new(n);

        for iy in 0..self.nav_ny {
            let y = self.nav_min_y + iy as f64 * NAV_STEP;
            for ix in 0..self.nav_nx {
                let x = self.nav_min_x + ix as f64 * NAV_STEP;
                let tz = self.terrain_z(x, y);
                let idx = iy * self.nav_nx + ix;
                self.nav_z[idx] = tz;
                let slope_deg = self.terrain_slope_deg(x, y);
                if slope_deg > 48.0 {
                    self.nav_walkable[idx] = false;
                } else {
                    self.nav_walkable[idx] = !self.is_cylinder_blocked(x, y, tz, 2.7, 9.0, 1.2);
                    if self.nav_walkable[idx] {
                        // Compute minimum overhang headroom above ground (bridges 4..8!)
                        let mut min_hr = 999.0f64;
                        if let Some((gx, gy)) = self.grid_coords(x, y) {
                            for &pid in &self.prism_grid[gy * self.grid_nx + gx] {
                                let p = &self.prisms[pid as usize];
                                if p.base > tz + 1.2 && (self.point_in_prism_2d(pid as usize, x, y) || dist_sq_to_ring(x, y, &p.outer) < 12.0) {
                                    min_hr = min_hr.min(p.base - tz);
                                }
                            }
                        }
                        self.nav_headroom[idx] = min_hr as f32;
                    }
                }
            }
        }

        // Precompute cover score (fraction of 8 neighbors that are building walls!)
        for iy in 1..self.nav_ny.saturating_sub(1) {
            for ix in 1..self.nav_nx.saturating_sub(1) {
                let idx = iy * self.nav_nx + ix;
                if !self.nav_walkable[idx] {
                    continue;
                }
                let mut adj_wall = 0.0f32;
                for dy in -2i32..=2 {
                    for dx in -2i32..=2 {
                        if dx == 0 && dy == 0 { continue; }
                        let nx = ix as i32 + dx;
                        let ny = iy as i32 + dy;
                        if nx >= 0 && nx < self.nav_nx as i32 && ny >= 0 && ny < self.nav_ny as i32 {
                            if !self.nav_walkable[ny as usize * self.nav_nx + nx as usize] {
                                let d2 = (dx * dx + dy * dy) as f32;
                                adj_wall += 1.0 / d2;
                            }
                        }
                    }
                }
                self.nav_cover[idx] = adj_wall.min(4.0) * 0.25;
            }
        }
    }

    #[inline]
    pub fn terrain_z(&self, x: f64, y: f64) -> f64 {
        if self.t_nx < 2 || self.t_ny < 2 {
            return 0.0;
        }
        let fx = (x - self.t_min_x) / self.t_step;
        let fy = (y - self.t_min_y) / self.t_step;
        let i = (fx.floor() as isize).clamp(0, (self.t_nx - 2) as isize) as usize;
        let j = (fy.floor() as isize).clamp(0, (self.t_ny - 2) as isize) as usize;
        let tx = (fx - i as f64).clamp(0.0, 1.0);
        let ty = (fy - j as f64).clamp(0.0, 1.0);
        let z00 = self.terrain[j * self.t_nx + i] as f64 * self.t_scale;
        let z10 = self.terrain[j * self.t_nx + i + 1] as f64 * self.t_scale;
        let z01 = self.terrain[(j + 1) * self.t_nx + i] as f64 * self.t_scale;
        let z11 = self.terrain[(j + 1) * self.t_nx + i + 1] as f64 * self.t_scale;
        (z00 * (1.0 - tx) + z10 * tx) * (1.0 - ty) + (z01 * (1.0 - tx) + z11 * tx) * ty
    }

    pub fn terrain_slope_deg(&self, x: f64, y: f64) -> f64 {
        let h = 1.0;
        let dz_dx = (self.terrain_z(x + h, y) - self.terrain_z(x - h, y)) / (2.0 * h);
        let dz_dy = (self.terrain_z(x, y + h) - self.terrain_z(x, y - h)) / (2.0 * h);
        dz_dx.hypot(dz_dy).atan().to_degrees()
    }

    #[inline]
    fn grid_coords(&self, x: f64, y: f64) -> Option<(usize, usize)> {
        let gx = ((x - self.grid_min_x) / GRID_CELL).floor() as isize;
        let gy = ((y - self.grid_min_y) / GRID_CELL).floor() as isize;
        if gx >= 0 && gx < self.grid_nx as isize && gy >= 0 && gy < self.grid_ny as isize {
            Some((gx as usize, gy as usize))
        } else {
            None
        }
    }

    #[inline]
    pub fn point_in_prism_2d(&self, prism_idx: usize, x: f64, y: f64) -> bool {
        let p = &self.prisms[prism_idx];
        if x < p.min_x || x > p.max_x || y < p.min_y || y > p.max_y {
            return false;
        }
        if !point_in_ring(x, y, &p.outer) {
            return false;
        }
        for hole in &p.holes {
            if point_in_ring(x, y, hole) {
                return false;
            }
        }
        true
    }

    pub fn is_covered_above(&self, x: f64, y: f64, z_low: f64, headroom: f64) -> bool {
        if let Some((gx, gy)) = self.grid_coords(x, y) {
            for &pid in &self.prism_grid[gy * self.grid_nx + gx] {
                let p = &self.prisms[pid as usize];
                if p.top > z_low && p.base < z_low + headroom && self.point_in_prism_2d(pid as usize, x, y) {
                    return true;
                }
            }
        }
        false
    }

    /// O(1) lookup: returns index into `self.rooftops` if `(x, y, feet_z)` is standing on a tracked rooftop!
    #[inline]
    pub fn rooftop_at(&self, x: f64, y: f64, feet_z: f64) -> Option<usize> {
        let (gx, gy) = self.grid_coords(x, y)?;
        for &pid in &self.prism_grid[gy * self.grid_nx + gx] {
            let p_idx = pid as usize;
            if let Some(r_idx) = self.prism_to_roof[p_idx] {
                let p = &self.prisms[p_idx];
                if (p.top - feet_z).abs() < 1.3 && self.point_in_prism_2d(p_idx, x, y) {
                    return Some(r_idx as usize);
                }
            }
        }
        None
    }

    /// Returns the highest tracked rooftop covering `(x, y)` (for Phase 5-6 pin fortress check!).
    pub fn rooftop_covering(&self, x: f64, y: f64) -> Option<usize> {
        let (gx, gy) = self.grid_coords(x, y)?;
        let mut best: Option<(f64, usize)> = None;
        for &pid in &self.prism_grid[gy * self.grid_nx + gx] {
            let p_idx = pid as usize;
            if let Some(r_idx) = self.prism_to_roof[p_idx] {
                let p = &self.prisms[p_idx];
                if self.point_in_prism_2d(p_idx, x, y) {
                    if best.map(|(bt, _)| p.top > bt).unwrap_or(true) {
                        best = Some((p.top, r_idx as usize));
                    }
                }
            }
        }
        best.map(|(_, idx)| idx)
    }

    /// Support floor height under (x, y) reachable from feet_z (stepping up by step_up).
    pub fn support_z(&self, x: f64, y: f64, feet_z: f64, step_up: f64, mech_h: f64) -> f64 {
        let mut best = self.terrain_z(x, y);
        if let Some((gx, gy)) = self.grid_coords(x, y) {
            for &pid in &self.prism_grid[gy * self.grid_nx + gx] {
                let p = &self.prisms[pid as usize];
                if p.top <= feet_z + step_up + 0.05 && p.top > best && self.point_in_prism_2d(pid as usize, x, y) {
                    if !self.is_covered_above(x, y, p.top + 0.1, mech_h - 0.1) {
                        best = p.top;
                    }
                }
            }
        }
        best
    }

    /// Highest prism top at (x, y) (or terrain if none).
    pub fn highest_surface_z(&self, x: f64, y: f64) -> (f64, Option<usize>) {
        let mut best = self.terrain_z(x, y);
        let mut best_p = None;
        if let Some((gx, gy)) = self.grid_coords(x, y) {
            for &pid in &self.prism_grid[gy * self.grid_nx + gx] {
                let p = &self.prisms[pid as usize];
                if p.top > best && self.point_in_prism_2d(pid as usize, x, y) {
                    best = p.top;
                    best_p = Some(pid as usize);
                }
            }
        }
        (best, best_p)
    }

    /// True if a vertical cylinder at (x, y, feet_z) of radius r and height h intersects any solid prism.
    pub fn is_cylinder_blocked(&self, x: f64, y: f64, feet_z: f64, r: f64, h: f64, step_up: f64) -> bool {
        if x < self.min_x + r || x > self.max_x - r || y < self.min_y + r || y > self.max_y - r {
            return true;
        }
        let gx0 = (((x - r) - self.grid_min_x) / GRID_CELL).floor().max(0.0) as usize;
        let gx1 = ((((x + r) - self.grid_min_x) / GRID_CELL).floor().max(0.0) as usize).min(self.grid_nx - 1);
        let gy0 = (((y - r) - self.grid_min_y) / GRID_CELL).floor().max(0.0) as usize;
        let gy1 = ((((y + r) - self.grid_min_y) / GRID_CELL).floor().max(0.0) as usize).min(self.grid_ny - 1);
        let r2 = r * r;
        for gy in gy0..=gy1 {
            for gx in gx0..=gx1 {
                for &pid in &self.prism_grid[gy * self.grid_nx + gx] {
                    let p = &self.prisms[pid as usize];
                    // Vertical overlap check: prism top must be above feet+step_up, base below head
                    if p.top <= feet_z + step_up || p.base >= feet_z + h {
                        continue;
                    }
                    if x + r < p.min_x || x - r > p.max_x || y + r < p.min_y || y - r > p.max_y {
                        continue;
                    }
                    if self.point_in_prism_2d(pid as usize, x, y) {
                        return true;
                    }
                    if dist_sq_to_ring(x, y, &p.outer) < r2 {
                        return true;
                    }
                    for hole in &p.holes {
                        if dist_sq_to_ring(x, y, hole) < r2 {
                            return true;
                        }
                    }
                }
            }
        }
        false
    }

    /// Wall push-out vector for smooth local steering around buildings.
    pub fn wall_repulsion(&self, x: f64, y: f64, feet_z: f64, check_r: f64, h: f64, step_up: f64) -> [f64; 2] {
        let mut rx = 0.0;
        let mut ry = 0.0;
        let gx0 = (((x - check_r) - self.grid_min_x) / GRID_CELL).floor().max(0.0) as usize;
        let gx1 = ((((x + check_r) - self.grid_min_x) / GRID_CELL).floor().max(0.0) as usize).min(self.grid_nx - 1);
        let gy0 = (((y - check_r) - self.grid_min_y) / GRID_CELL).floor().max(0.0) as usize;
        let gy1 = ((((y + check_r) - self.grid_min_y) / GRID_CELL).floor().max(0.0) as usize).min(self.grid_ny - 1);
        let r2 = check_r * check_r;
        let mut seen = [u16::MAX; 16];
        let mut seen_n = 0usize;

        for gy in gy0..=gy1 {
            for gx in gx0..=gx1 {
                for &pid in &self.prism_grid[gy * self.grid_nx + gx] {
                    if seen[..seen_n].contains(&pid) { continue; }
                    if seen_n < seen.len() { seen[seen_n] = pid; seen_n += 1; }
                    let p = &self.prisms[pid as usize];
                    if p.top <= feet_z + step_up || p.base >= feet_z + h {
                        continue;
                    }
                    if x + check_r < p.min_x || x - check_r > p.max_x || y + check_r < p.min_y || y - check_r > p.max_y {
                        continue;
                    }
                    let (cx, cy, d2) = closest_point_on_ring(x, y, &p.outer);
                    if d2 < r2 && d2 > 1e-8 {
                        let d = d2.sqrt();
                        let w = (check_r - d) / check_r;
                        rx += ((x - cx) / d) * w;
                        ry += ((y - cy) / d) * w;
                    }
                    for hole in &p.holes {
                        let (hx, hy, hd2) = closest_point_on_ring(x, y, hole);
                        if hd2 < r2 && hd2 > 1e-8 {
                            let d = hd2.sqrt();
                            let w = (check_r - d) / check_r;
                            rx += ((x - hx) / d) * w;
                            ry += ((y - hy) / d) * w;
                        }
                    }
                }
            }
        }
        [rx, ry]
    }

    /// Exact 3D raycast against all prisms and terrain.
    pub fn raycast_solid(&self, origin: [f64; 3], dir: [f64; 3], max_dist: f64) -> Option<RayHit> {
        if max_dist <= 0.0 || !max_dist.is_finite() {
            return None;
        }
        let mut best_t = max_dist;
        let mut best_prism: Option<usize> = None;
        let mut hit_any = false;

        // 1. Traverse 2D prism grid via DDA
        let end_x = origin[0] + dir[0] * max_dist;
        let end_y = origin[1] + dir[1] * max_dist;
        let fx0 = (origin[0] - self.grid_min_x) / GRID_CELL;
        let fy0 = (origin[1] - self.grid_min_y) / GRID_CELL;
        let fx1 = (end_x - self.grid_min_x) / GRID_CELL;
        let fy1 = (end_y - self.grid_min_y) / GRID_CELL;

        let mut ix = fx0.floor() as isize;
        let mut iy = fy0.floor() as isize;
        let end_ix = fx1.floor() as isize;
        let end_iy = fy1.floor() as isize;

        let step_x: isize = if dir[0] > 1e-9 { 1 } else if dir[0] < -1e-9 { -1 } else { 0 };
        let step_y: isize = if dir[1] > 1e-9 { 1 } else if dir[1] < -1e-9 { -1 } else { 0 };

        let t_delta_x = if step_x != 0 { (GRID_CELL / dir[0]).abs() } else { f64::INFINITY };
        let t_delta_y = if step_y != 0 { (GRID_CELL / dir[1]).abs() } else { f64::INFINITY };

        let mut t_max_x = if step_x > 0 {
            (((ix + 1) as f64 * GRID_CELL + self.grid_min_x) - origin[0]) / dir[0]
        } else if step_x < 0 {
            ((ix as f64 * GRID_CELL + self.grid_min_x) - origin[0]) / dir[0]
        } else {
            f64::INFINITY
        };
        let mut t_max_y = if step_y > 0 {
            (((iy + 1) as f64 * GRID_CELL + self.grid_min_y) - origin[1]) / dir[1]
        } else if step_y < 0 {
            ((iy as f64 * GRID_CELL + self.grid_min_y) - origin[1]) / dir[1]
        } else {
            f64::INFINITY
        };

        let mut tested = [0u64; 16]; // bitset for up to 1024 prisms! Zero heap allocs!
        let max_steps = ((end_ix - ix).abs() + (end_iy - iy).abs() + 2).min(200);

        for _ in 0..max_steps {
            if ix >= 0 && ix < self.grid_nx as isize && iy >= 0 && iy < self.grid_ny as isize {
                let cell = &self.prism_grid[iy as usize * self.grid_nx + ix as usize];
                for &pid in cell {
                    let p_idx = pid as usize;
                    let word = p_idx >> 6;
                    let bit = 1u64 << (p_idx & 63);
                    if word < tested.len() {
                        if (tested[word] & bit) != 0 {
                            continue;
                        }
                        tested[word] |= bit;
                    }
                    if let Some(t_hit) = self.ray_vs_prism(p_idx, origin, dir, best_t) {
                        if t_hit < best_t {
                            best_t = t_hit;
                            best_prism = Some(p_idx);
                            hit_any = true;
                        }
                    }
                }
            }
            if ix == end_ix && iy == end_iy {
                break;
            }
            let t_next = t_max_x.min(t_max_y);
            if t_next > best_t {
                break;
            }
            if t_max_x < t_max_y {
                ix += step_x;
                t_max_x += t_delta_x;
            } else {
                iy += step_y;
                t_max_y += t_delta_y;
            }
        }

        // 2. Check terrain intersection up to best_t (skip when ray stays strictly above max_terrain_z; adaptive step!)
        let end_z = origin[2] + dir[2] * best_t;
        if !(origin[2] > self.max_terrain_z + 0.5 && end_z > self.max_terrain_z + 0.5) {
            let mut prev_t: f64 = 0.0;
            let mut t = (2.0f64).min(best_t);
            while prev_t < best_t {
                let x = origin[0] + dir[0] * t;
                let y = origin[1] + dir[1] * t;
                let z = origin[2] + dir[2] * t;
                let tz = self.terrain_z(x, y);
                if z <= tz {
                    let mut lo = prev_t;
                    let mut hi = t;
                    for _ in 0..6 {
                        let mid = 0.5 * (lo + hi);
                        let mx = origin[0] + dir[0] * mid;
                        let my = origin[1] + dir[1] * mid;
                        let mz = origin[2] + dir[2] * mid;
                        if mz <= self.terrain_z(mx, my) {
                            hi = mid;
                        } else {
                            lo = mid;
                        }
                    }
                    if hi < best_t {
                        best_t = hi;
                        best_prism = None;
                        hit_any = true;
                    }
                    break;
                }
                if t >= best_t {
                    break;
                }
                let step = if z - tz > 4.5 { 3.8 } else { 1.8 };
                prev_t = t;
                t = (t + step).min(best_t);
            }
        }

        if hit_any {
            Some(RayHit {
                t: best_t,
                pos: [
                    origin[0] + dir[0] * best_t,
                    origin[1] + dir[1] * best_t,
                    origin[2] + dir[2] * best_t,
                ],
                prism_id: best_prism,
            })
        } else {
            None
        }
    }

    fn ray_vs_prism(&self, p_idx: usize, o: [f64; 3], d: [f64; 3], max_t: f64) -> Option<f64> {
        let p = &self.prisms[p_idx];
        // Fast 3D AABB slab test
        let (mut tmin, mut tmax) = (0.0f64, max_t);
        for axis in 0..3 {
            let (bmin, bmax) = match axis {
                0 => (p.min_x, p.max_x),
                1 => (p.min_y, p.max_y),
                _ => (p.base, p.top),
            };
            if d[axis].abs() < 1e-12 {
                if o[axis] < bmin || o[axis] > bmax {
                    return None;
                }
            } else {
                let inv = 1.0 / d[axis];
                let mut t1 = (bmin - o[axis]) * inv;
                let mut t2 = (bmax - o[axis]) * inv;
                if t1 > t2 {
                    std::mem::swap(&mut t1, &mut t2);
                }
                tmin = tmin.max(t1);
                tmax = tmax.min(t2);
                if tmin > tmax {
                    return None;
                }
            }
        }

        // Check if origin itself is inside the prism
        if o[2] >= p.base && o[2] <= p.top && self.point_in_prism_2d(p_idx, o[0], o[1]) {
            return Some(0.0);
        }

        let mut best = f64::INFINITY;

        // Top & bottom caps
        if d[2].abs() > 1e-12 {
            for cap_z in [p.top, p.base] {
                let t = (cap_z - o[2]) / d[2];
                if t >= 0.0 && t < best && t <= max_t {
                    let hx = o[0] + d[0] * t;
                    let hy = o[1] + d[1] * t;
                    if self.point_in_prism_2d(p_idx, hx, hy) {
                        best = t;
                    }
                }
            }
        }

        // Side walls (outer ring + holes)
        best = best.min(ray_vs_ring_walls(o, d, p.base, p.top, &p.outer, best.min(max_t)));
        for hole in &p.holes {
            best = best.min(ray_vs_ring_walls(o, d, p.base, p.top, hole, best.min(max_t)));
        }

        if best <= max_t {
            Some(best)
        } else {
            None
        }
    }

    /// True if direct shot line from `a` to `b` is unobstructed by buildings, bridges, or terrain.
    #[inline]
    pub fn has_shot_los(&self, a: [f64; 3], b: [f64; 3]) -> bool {
        let dx = b[0] - a[0];
        let dy = b[1] - a[1];
        let dz = b[2] - a[2];
        let dist = (dx * dx + dy * dy + dz * dz).sqrt();
        if dist < 0.25 {
            return true;
        }
        let inv = 1.0 / dist;
        let dir = [dx * inv, dy * inv, dz * inv];
        self.raycast_solid(a, dir, dist - 0.15).is_none()
    }

    /// Checks whether trees (trunks or dense foliage) block vision along segment a -> b.
    pub fn trees_block_vision(&self, a: [f64; 3], b: [f64; 3]) -> bool {
        let dx = b[0] - a[0];
        let dy = b[1] - a[1];
        let dz = b[2] - a[2];
        let dist = (dx * dx + dy * dy + dz * dz).sqrt();
        if dist < 0.5 {
            return false;
        }
        let inv = 1.0 / dist;
        let dir = [dx * inv, dy * inv, dz * inv];
        let max_canopy_len = -(self.canopy_block_t.max(1e-4)).ln() / self.canopy_density.max(1e-4);

        let min_x = a[0].min(b[0]) - 6.0;
        let max_x = a[0].max(b[0]) + 6.0;
        let min_y = a[1].min(b[1]) - 6.0;
        let max_y = a[1].max(b[1]) + 6.0;
        let gx0 = ((min_x - self.grid_min_x) / GRID_CELL).floor().max(0.0) as usize;
        let gx1 = (((max_x - self.grid_min_x) / GRID_CELL).floor().max(0.0) as usize).min(self.grid_nx - 1);
        let gy0 = ((min_y - self.grid_min_y) / GRID_CELL).floor().max(0.0) as usize;
        let gy1 = (((max_y - self.grid_min_y) / GRID_CELL).floor().max(0.0) as usize).min(self.grid_ny - 1);

        let mut canopy_len = 0.0;
        let mut seen_trees = [0u64; 64]; // bitset for up to 4096 trees (zero double-counting!)
        // Traverse only cells near line (skip far cells in AABB)
        let nx_perp = -dir[1];
        let ny_perp = dir[0];
        let hlen = (dir[0] * dir[0] + dir[1] * dir[1]).sqrt();

        for gy in gy0..=gy1 {
            let cy = self.grid_min_y + (gy as f64 + 0.5) * GRID_CELL;
            for gx in gx0..=gx1 {
                let cx = self.grid_min_x + (gx as f64 + 0.5) * GRID_CELL;
                if hlen > 1e-6 {
                    let perp = ((cx - a[0]) * nx_perp + (cy - a[1]) * ny_perp).abs() / hlen;
                    if perp > GRID_CELL * 1.15 {
                        continue;
                    }
                }
                for &tid in &self.tree_grid[gy * self.grid_nx + gx] {
                    let tu = tid as usize;
                    let word = tu >> 6;
                    let bit = 1u64 << (tu & 63);
                    if word < seen_trees.len() {
                        if (seen_trees[word] & bit) != 0 { continue; }
                        seen_trees[word] |= bit;
                    }
                    let tr = &self.trees[tu];
                    // Trunk cylinder check
                    if let Some((t0, t1)) = ray_cylinder_z(a, dir, dist, tr.x, tr.y, tr.trunk_r, tr.z, tr.crown_base) {
                        if t1 > t0 {
                            return true;
                        }
                    }
                    // Crown sphere/cylinder approximation
                    let crown_mid_z = 0.5 * (tr.crown_base + (tr.z + tr.height));
                    let crown_rz = 0.5 * ((tr.z + tr.height) - tr.crown_base).max(1.0);
                    if let Some((t0, t1)) = ray_ellipsoid(a, dir, dist, tr.crown_x, tr.crown_y, crown_mid_z, tr.crown_r, crown_rz) {
                        canopy_len += (t1 - t0).max(0.0);
                        if canopy_len >= max_canopy_len {
                            return true;
                        }
                    }
                }
            }
        }
        false
    }

    /// True if an observer at `eye` cannot see a target mech at `target_pos` (height `target_h`)
    /// because solids or tree trunks/canopy block BOTH center (0.5*h) and top (1.0*h) per protocol.md §8.
    pub fn mech_vision_blocked(&self, eye: [f64; 3], target_pos: [f64; 3], target_h: f64) -> bool {
        let mid = [target_pos[0], target_pos[1], target_pos[2] + 0.5 * target_h];
        if self.has_shot_los(eye, mid) && !self.trees_block_vision(eye, mid) {
            return false;
        }
        let top = [target_pos[0], target_pos[1], target_pos[2] + target_h];
        !(self.has_shot_los(eye, top) && !self.trees_block_vision(eye, top))
    }

    /// Check if a parabolic mortar trajectory clears solid obstacles up to 95% of flight time.
    pub fn mortar_arc_clear(&self, origin: [f64; 3], vel: [f64; 3], gravity: f64, flight_t: f64) -> bool {
        let steps = 14;
        let check_t = (flight_t * 0.95).max(0.15);
        let mut prev = origin;
        for i in 1..=steps {
            let t = check_t * (i as f64 / steps as f64);
            let cur = [
                origin[0] + vel[0] * t,
                origin[1] + vel[1] * t,
                origin[2] + vel[2] * t - 0.5 * gravity * t * t,
            ];
            if !self.has_shot_los(prev, cur) {
                return false;
            }
            prev = cur;
        }
        true
    }

    /// Fast 2D A* on the 8m navigation grid with dynamic mech radius/height (cube-safe bridges!) & closed skip!
    pub fn next_waypoint(&self, from: [f64; 2], to: [f64; 2], feet_z: f64) -> [f64; 2] {
        self.next_waypoint_sized(from, to, feet_z, 2.7, 9.2, 1.2)
    }

    pub fn next_waypoint_sized(
        &self,
        from: [f64; 2],
        to: [f64; 2],
        feet_z: f64,
        mech_r: f64,
        mech_h: f64,
        step_up: f64,
    ) -> [f64; 2] {
        let d = (to[0] - from[0]).hypot(to[1] - from[1]);
        if d < 5.0 {
            return to;
        }
        // Check straight segment using ground tz (fixes downhill wall-blindness!)
        let sample_n = (d / 4.0).ceil().clamp(2.0, 24.0) as usize;
        let mut clear = true;
        let from_tz = self.terrain_z(from[0], from[1]);
        let on_roof = feet_z > from_tz + 3.0;
        for i in 1..sample_n {
            let frac = i as f64 / sample_n as f64;
            let sx = from[0] + (to[0] - from[0]) * frac;
            let sy = from[1] + (to[1] - from[1]) * frac;
            let tz = self.terrain_z(sx, sy);
            let chk_z = if on_roof { self.support_z(sx, sy, feet_z, step_up, mech_h) } else { tz };
            if (!on_roof && self.terrain_slope_deg(sx, sy) > 48.0)
                || self.is_cylinder_blocked(sx, sy, chk_z, mech_r + 0.2, mech_h, step_up)
            {
                clear = false;
                break;
            }
        }
        if clear {
            return to;
        }

        if self.nav_nx < 2 || self.nav_ny < 2 {
            return to;
        }

        let start = self.nearest_walkable_cell_visible(from, feet_z, mech_r, mech_h, step_up);
        let goal = self.nearest_walkable_cell(to, mech_h as f32);
        if start == goal {
            return to;
        }

        let n = self.nav_nx * self.nav_ny;
        let mut scratch = self.astar_scratch.borrow_mut();
        let gen = scratch.reset(n);

        let s_idx = start.1 * self.nav_nx + start.0;
        let g_idx = goal.1 * self.nav_nx + goal.0;
        scratch.seen_gen[s_idx] = gen;
        scratch.g_score[s_idx] = 0.0;
        scratch.came_from[s_idx] = u32::MAX;
        scratch.heap.push(AStarNode {
            f: octile(start, goal),
            idx: s_idx as u32,
        });

        let dirs: [(isize, isize, f32); 8] = [
            (1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
            (1, 1, 1.4142), (1, -1, 1.4142), (-1, 1, 1.4142), (-1, -1, 1.4142),
        ];

        let mut expanded = 0usize;
        let mut best_closest = s_idx;
        let mut best_h = octile(start, goal);
        let need_hr = mech_h as f32 + 0.15;

        while let Some(cur) = scratch.heap.pop() {
            let ci = cur.idx as usize;
            let cx = ci % self.nav_nx;
            let cy = ci / self.nav_nx;
            let cg = scratch.g_score[ci];
            let ch = octile((cx, cy), goal);
            if cur.f > cg + ch + 1e-3 {
                continue;
            }
            if ci == g_idx {
                best_closest = g_idx;
                break;
            }
            expanded += 1;
            if expanded > 1600 {
                break;
            }
            if ch < best_h {
                best_h = ch;
                best_closest = ci;
            }
            for &(dx, dy, cost) in &dirs {
                let nx = cx as isize + dx;
                let ny = cy as isize + dy;
                if nx < 0 || nx >= self.nav_nx as isize || ny < 0 || ny >= self.nav_ny as isize {
                    continue;
                }
                let ux = nx as usize;
                let uy = ny as usize;
                let ni = uy * self.nav_nx + ux;
                if !self.nav_walkable[ni] || self.nav_headroom[ni] < need_hr {
                    continue;
                }
                if (self.nav_z[ni] - self.nav_z[ci]).abs() > 4.5 {
                    continue;
                }
                if dx != 0 && dy != 0 {
                    let i1 = cy * self.nav_nx + ux;
                    let i2 = uy * self.nav_nx + cx;
                    if !self.nav_walkable[i1] || self.nav_headroom[i1] < need_hr
                        || !self.nav_walkable[i2] || self.nav_headroom[i2] < need_hr
                    {
                        continue;
                    }
                }
                let ng = cg + cost;
                let prev_g = if scratch.seen_gen[ni] == gen { scratch.g_score[ni] } else { f32::INFINITY };
                if ng < prev_g {
                    scratch.seen_gen[ni] = gen;
                    scratch.g_score[ni] = ng;
                    scratch.came_from[ni] = ci as u32;
                    scratch.heap.push(AStarNode {
                        f: ng + octile((ux, uy), goal),
                        idx: ni as u32,
                    });
                }
            }
        }

        if best_closest == s_idx {
            return to;
        }
        let mut cur = best_closest;
        while cur != s_idx && scratch.seen_gen[cur] == gen && scratch.came_from[cur] != u32::MAX {
            scratch.path.push(cur);
            cur = scratch.came_from[cur] as usize;
            if scratch.path.len() > 2048 { break; }
        }
        if scratch.path.is_empty() {
            return to;
        }
        scratch.path.reverse();

        let mut chosen = scratch.path[0];
        for &cell_idx in scratch.path.iter().take(6) {
            let wp = self.cell_to_world(cell_idx);
            let mut seg_ok = true;
            for &frac in &[0.33, 0.67] {
                let sx = from[0] + (wp[0] - from[0]) * frac;
                let sy = from[1] + (wp[1] - from[1]) * frac;
                let tz = self.terrain_z(sx, sy);
                if self.is_cylinder_blocked(sx, sy, tz, mech_r + 0.2, mech_h, step_up) {
                    seg_ok = false;
                    break;
                }
            }
            if seg_ok {
                chosen = cell_idx;
            } else {
                break;
            }
        }
        self.cell_to_world(chosen)
    }

    fn cell_to_world(&self, idx: usize) -> [f64; 2] {
        let ix = idx % self.nav_nx;
        let iy = idx / self.nav_nx;
        [
            self.nav_min_x + ix as f64 * NAV_STEP,
            self.nav_min_y + iy as f64 * NAV_STEP,
        ]
    }

    /// Snaps `from` to the nearest walkable cell without snapping through a solid wall when on the ground.
    fn nearest_walkable_cell_visible(
        &self,
        p: [f64; 2],
        feet_z: f64,
        _mech_r: f64,
        mech_h: f64,
        _step_up: f64,
    ) -> (usize, usize) {
        let need_hr = mech_h as f32;
        let ix = (((p[0] - self.nav_min_x) / NAV_STEP).round() as isize)
            .clamp(0, (self.nav_nx - 1) as isize) as usize;
        let iy = (((p[1] - self.nav_min_y) / NAV_STEP).round() as isize)
            .clamp(0, (self.nav_ny - 1) as isize) as usize;
        let ok = |ux: usize, uy: usize| {
            let idx = uy * self.nav_nx + ux;
            self.nav_walkable[idx] && self.nav_headroom[idx] >= need_hr
        };
        if ok(ix, iy) {
            return (ix, iy);
        }
        let on_ground = feet_z <= self.terrain_z(p[0], p[1]) + 2.5;
        let p_eye = [p[0], p[1], feet_z + 3.0];
        let mut fallback: Option<(usize, usize)> = None;
        let mut los_checks = 0usize;
        for rad in 1..=8isize {
            for dy in -rad..=rad {
                for dx in -rad..=rad {
                    if dx.abs() != rad && dy.abs() != rad { continue; }
                    let nx = ix as isize + dx;
                    let ny = iy as isize + dy;
                    if nx >= 0 && nx < self.nav_nx as isize && ny >= 0 && ny < self.nav_ny as isize {
                        let ux = nx as usize;
                        let uy = ny as usize;
                        if ok(ux, uy) {
                            if !on_ground {
                                return (ux, uy);
                            }
                            if fallback.is_none() {
                                fallback = Some((ux, uy));
                            }
                            if rad <= 2 && los_checks < 12 {
                                los_checks += 1;
                                let w = self.cell_to_world(uy * self.nav_nx + ux);
                                let wz = self.nav_z[uy * self.nav_nx + ux];
                                if self.has_shot_los(p_eye, [w[0], w[1], wz + 3.0]) {
                                    return (ux, uy);
                                }
                            }
                        }
                    }
                }
            }
            // Only fall back to non-LOS cell once rad >= 2 has been searched (never return a behind-wall rad=1 cell early!)
            if rad >= 2 && fallback.is_some() {
                return fallback.unwrap();
            }
        }
        fallback.unwrap_or((ix, iy))
    }

    fn nearest_walkable_cell(&self, p: [f64; 2], need_hr: f32) -> (usize, usize) {
        let ix = (((p[0] - self.nav_min_x) / NAV_STEP).round() as isize)
            .clamp(0, (self.nav_nx - 1) as isize) as usize;
        let iy = (((p[1] - self.nav_min_y) / NAV_STEP).round() as isize)
            .clamp(0, (self.nav_ny - 1) as isize) as usize;
        let ok = |ux: usize, uy: usize| {
            let idx = uy * self.nav_nx + ux;
            self.nav_walkable[idx] && self.nav_headroom[idx] >= need_hr
        };
        if ok(ix, iy) {
            return (ix, iy);
        }
        for rad in 1..=8isize {
            for dy in -rad..=rad {
                for dx in -rad..=rad {
                    if dx.abs() != rad && dy.abs() != rad { continue; }
                    let nx = ix as isize + dx;
                    let ny = iy as isize + dy;
                    if nx >= 0 && nx < self.nav_nx as isize && ny >= 0 && ny < self.nav_ny as isize {
                        let ux = nx as usize;
                        let uy = ny as usize;
                        if ok(ux, uy) {
                            return (ux, uy);
                        }
                    }
                }
            }
        }
        (ix, iy)
    }

    pub fn cover_at(&self, p: [f64; 2]) -> f32 {
        if self.nav_nx < 2 || self.nav_ny < 2 {
            return 0.0;
        }
        let ix = (((p[0] - self.nav_min_x) / NAV_STEP).round() as isize)
            .clamp(0, (self.nav_nx - 1) as isize) as usize;
        let iy = (((p[1] - self.nav_min_y) / NAV_STEP).round() as isize)
            .clamp(0, (self.nav_ny - 1) as isize) as usize;
        self.nav_cover[iy * self.nav_nx + ix]
    }
}

#[derive(Copy, Clone)]
struct AStarNode {
    f: f32,
    idx: u32,
}

impl Eq for AStarNode {}
impl PartialEq for AStarNode {
    fn eq(&self, other: &Self) -> bool {
        self.idx == other.idx && self.f.to_bits() == other.f.to_bits()
    }
}
impl Ord for AStarNode {
    fn cmp(&self, other: &Self) -> Ordering {
        other.f.partial_cmp(&self.f).unwrap_or(Ordering::Equal)
    }
}
impl PartialOrd for AStarNode {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> { Some(self.cmp(other)) }
}

#[inline]
fn octile(a: (usize, usize), b: (usize, usize)) -> f32 {
    let dx = (a.0 as isize - b.0 as isize).abs() as f32;
    let dy = (a.1 as isize - b.1 as isize).abs() as f32;
    let (mn, mx) = if dx < dy { (dx, dy) } else { (dy, dx) };
    mx + 0.4142 * mn
}

fn flat_to_ring(flat: &[f64]) -> Vec<[f64; 2]> {
    let n = flat.len() / 2;
    let mut ring = Vec::with_capacity(n);
    for i in 0..n {
        ring.push([flat[2 * i], flat[2 * i + 1]]);
    }
    // Drop closing duplicate vertex if present
    if ring.len() > 2 {
        let first = ring[0];
        let last = ring[ring.len() - 1];
        if (first[0] - last[0]).abs() < 1e-6 && (first[1] - last[1]).abs() < 1e-6 {
            ring.pop();
        }
    }
    ring
}

fn polygon_area_centroid(ring: &[[f64; 2]]) -> (f64, [f64; 2]) {
    let n = ring.len();
    if n < 3 {
        return (0.0, [0.0, 0.0]);
    }
    let mut a2 = 0.0;
    let mut cx = 0.0;
    let mut cy = 0.0;
    for i in 0..n {
        let p0 = ring[i];
        let p1 = ring[(i + 1) % n];
        let cross = p0[0] * p1[1] - p1[0] * p0[1];
        a2 += cross;
        cx += (p0[0] + p1[0]) * cross;
        cy += (p0[1] + p1[1]) * cross;
    }
    if a2.abs() < 1e-6 {
        (0.0, ring[0])
    } else {
        (a2.abs() * 0.5, [cx / (3.0 * a2), cy / (3.0 * a2)])
    }
}

#[inline]
pub fn point_in_ring(x: f64, y: f64, ring: &[[f64; 2]]) -> bool {
    let n = ring.len();
    if n < 3 {
        return false;
    }
    let mut inside = false;
    let mut j = n - 1;
    for i in 0..n {
        let xi = ring[i][0];
        let yi = ring[i][1];
        let xj = ring[j][0];
        let yj = ring[j][1];
        let intersect = ((yi > y) != (yj > y))
            && (x < (xj - xi) * (y - yi) / (yj - yi) + xi);
        if intersect {
            inside = !inside;
        }
        j = i;
    }
    inside
}

#[inline]
fn closest_point_on_ring(x: f64, y: f64, ring: &[[f64; 2]]) -> (f64, f64, f64) {
    let n = ring.len();
    let mut best_d2 = f64::INFINITY;
    let mut best_x = x;
    let mut best_y = y;
    for i in 0..n {
        let a = ring[i];
        let b = ring[(i + 1) % n];
        let abx = b[0] - a[0];
        let aby = b[1] - a[1];
        let denom = abx * abx + aby * aby;
        let t = if denom > 1e-12 {
            (((x - a[0]) * abx + (y - a[1]) * aby) / denom).clamp(0.0, 1.0)
        } else {
            0.0
        };
        let px = a[0] + abx * t;
        let py = a[1] + aby * t;
        let d2 = (x - px) * (x - px) + (y - py) * (y - py);
        if d2 < best_d2 {
            best_d2 = d2;
            best_x = px;
            best_y = py;
        }
    }
    (best_x, best_y, best_d2)
}

#[inline]
fn dist_sq_to_ring(x: f64, y: f64, ring: &[[f64; 2]]) -> f64 {
    closest_point_on_ring(x, y, ring).2
}

#[inline]
fn ray_vs_ring_walls(o: [f64; 3], d: [f64; 3], base: f64, top: f64, ring: &[[f64; 2]], max_t: f64) -> f64 {
    let mut best = f64::INFINITY;
    let n = ring.len();
    for i in 0..n {
        let p0 = ring[i];
        let p1 = ring[(i + 1) % n];
        let sx = p1[0] - p0[0];
        let sy = p1[1] - p0[1];
        let denom = d[0] * sy - d[1] * sx;
        if denom.abs() < 1e-12 {
            continue;
        }
        let qx = p0[0] - o[0];
        let qy = p0[1] - o[1];
        let t = (qx * sy - qy * sx) / denom;
        if t < 0.0 || t >= best || t > max_t {
            continue;
        }
        let u = (qx * d[1] - qy * d[0]) / denom;
        if u < 0.0 || u > 1.0 {
            continue;
        }
        let hz = o[2] + d[2] * t;
        if hz >= base && hz <= top {
            best = t;
        }
    }
    best
}

fn ray_cylinder_z(
    o: [f64; 3],
    d: [f64; 3],
    max_t: f64,
    cx: f64,
    cy: f64,
    r: f64,
    z_min: f64,
    z_max: f64,
) -> Option<(f64, f64)> {
    let ox = o[0] - cx;
    let oy = o[1] - cy;
    let a = d[0] * d[0] + d[1] * d[1];
    let b = 2.0 * (ox * d[0] + oy * d[1]);
    let c = ox * ox + oy * oy - r * r;
    let (mut t0, mut t1) = if a < 1e-12 {
        if c <= 0.0 { (0.0, max_t) } else { return None; }
    } else {
        let disc = b * b - 4.0 * a * c;
        if disc < 0.0 { return None; }
        let s = disc.sqrt();
        ((-b - s) / (2.0 * a), (-b + s) / (2.0 * a))
    };
    t0 = t0.max(0.0);
    t1 = t1.min(max_t);
    if t0 >= t1 { return None; }
    if d[2].abs() < 1e-12 {
        if o[2] < z_min || o[2] > z_max { return None; }
    } else {
        let mut tz0 = (z_min - o[2]) / d[2];
        let mut tz1 = (z_max - o[2]) / d[2];
        if tz0 > tz1 { std::mem::swap(&mut tz0, &mut tz1); }
        t0 = t0.max(tz0);
        t1 = t1.min(tz1);
        if t0 >= t1 { return None; }
    }
    Some((t0, t1))
}

fn ray_ellipsoid(
    o: [f64; 3],
    d: [f64; 3],
    max_t: f64,
    cx: f64,
    cy: f64,
    cz: f64,
    rxy: f64,
    rz: f64,
) -> Option<(f64, f64)> {
    let inv_xy = 1.0 / rxy.max(0.1);
    let inv_z = 1.0 / rz.max(0.1);
    let ox = (o[0] - cx) * inv_xy;
    let oy = (o[1] - cy) * inv_xy;
    let oz = (o[2] - cz) * inv_z;
    let dx = d[0] * inv_xy;
    let dy = d[1] * inv_xy;
    let dz = d[2] * inv_z;
    let a = dx * dx + dy * dy + dz * dz;
    if a < 1e-12 { return None; }
    let b = 2.0 * (ox * dx + oy * dy + oz * dz);
    let c = ox * ox + oy * oy + oz * oz - 1.0;
    let disc = b * b - 4.0 * a * c;
    if disc < 0.0 { return None; }
    let s = disc.sqrt();
    let t0 = ((-b - s) / (2.0 * a)).max(0.0);
    let t1 = ((-b + s) / (2.0 * a)).min(max_t);
    if t0 < t1 { Some((t0, t1)) } else { None }
}
