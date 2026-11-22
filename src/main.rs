mod combat;
mod map;
mod protocol;
mod tactics;
mod world_state;

use map::ArenaMap;
use protocol::*;
use std::io::{self, BufRead, BufWriter, Write};
use std::panic::{self, AssertUnwindSafe};
use std::sync::mpsc;
use std::thread;
use world_state::WorldState;

const BOT_NAME: &str = "el-clasico";

struct BotRuntime {
    map: ArenaMap,
    state: WorldState,
    min_pitch: f64,
    max_pitch: f64,
}

impl BotRuntime {
    fn from_hello(hello: &Hello) -> Self {
        let map = match ArenaMap::load(&hello.map.dir) {
            Ok(m) => {
                eprintln!(
                    "[el-clasico] loaded map from {}: {} prisms, {} trees, {} rooftops",
                    hello.map.dir,
                    m.prisms.len(),
                    m.trees.len(),
                    m.rooftops.len()
                );
                m
            }
            Err(e) => {
                eprintln!("[el-clasico] map load failed ({}), using fallback", e);
                ArenaMap::fallback_empty()
            }
        };
        let min_pitch = hello.config.mech_f("min_pitch", -0.7);
        let max_pitch = hello.config.mech_f("max_pitch", 1.3);
        let state = WorldState::new(hello);
        BotRuntime {
            map,
            state,
            min_pitch,
            max_pitch,
        }
    }

    fn act(&mut self, obs: &Observation) -> Action {
        self.state.update(obs, &self.map);
        tactics::decide_action(obs, &mut self.state, &self.map)
    }
}

fn send_json<T: serde::Serialize>(out: &mut BufWriter<io::StdoutLock<'_>>, msg: &T) {
    if let Ok(line) = serde_json::to_string(msg) {
        let _ = out.write_all(line.as_bytes());
        let _ = out.write_all(b"\n");
        let _ = out.flush();
    }
}

fn main() {
    let (tx, rx) = mpsc::channel::<Option<String>>();

    // Background stdin reader so queued observations can be drained immediately (newest-observation rule!)
    thread::spawn(move || {
        let stdin = io::stdin();
        let mut handle = stdin.lock();
        let mut buf = String::with_capacity(16384);
        loop {
            buf.clear();
            match handle.read_line(&mut buf) {
                Ok(0) => {
                    let _ = tx.send(None);
                    break;
                }
                Ok(_) => {
                    if !buf.trim().is_empty() {
                        if tx.send(Some(buf.clone())).is_err() {
                            break;
                        }
                    }
                }
                Err(_) => {
                    let _ = tx.send(None);
                    break;
                }
            }
        }
    });

    let stdout = io::stdout();
    let mut out = BufWriter::with_capacity(8192, stdout.lock());

    let mut runtime: Option<BotRuntime> = None;
    let mut last_continuous_move: Option<[f64; 2]> = None;
    let mut last_continuous_aim: Option<[f64; 2]> = None;
    let mut last_continuous_boost: Option<bool> = None;
    let mut error_count: usize = 0;

    loop {
        // Block for first message, then immediately drain any queued lines!
        let first = match rx.recv() {
            Ok(m) => m,
            Err(_) => break,
        };
        let mut batch = Vec::with_capacity(8);
        batch.push(first);
        while let Ok(more) = rx.try_recv() {
            batch.push(more);
        }

        let mut latest_obs: Option<Observation> = None;
        let mut skipped_kills: i64 = 0;
        let mut should_exit = false;

        for item in batch {
            let line = match item {
                Some(l) => l,
                None => {
                    should_exit = true;
                    break;
                }
            };
            match serde_json::from_str::<ServerMessage>(&line) {
                Ok(ServerMessage::Hello(hello)) => {
                    let res = panic::catch_unwind(AssertUnwindSafe(|| BotRuntime::from_hello(&hello)));
                    match res {
                        Ok(rt) => runtime = Some(rt),
                        Err(_) => eprintln!("[el-clasico] panic during hello init!"),
                    }
                    send_json(
                        &mut out,
                        &ReadyMsg {
                            msg_type: "ready",
                            name: BOT_NAME.to_string(),
                        },
                    );
                }
                Ok(ServerMessage::Obs(obs)) => {
                    // Preserve mech kill counts from any queued obs we skip so FFA mode detection never false-triggers!
                    if let Some(prev_obs) = latest_obs.replace(obs) {
                        for ev in &prev_obs.events {
                            if ev.kind == "kill"
                                && ev.target.map(|tid| (0..100).contains(&tid)).unwrap_or(true)
                            {
                                skipped_kills += 1;
                            }
                        }
                    }
                }
                Ok(ServerMessage::End(_end)) => {
                    should_exit = true;
                    break;
                }
                Ok(ServerMessage::Unknown) => {}
                Err(e) => {
                    error_count += 1;
                    if error_count <= 5 {
                        eprintln!("[el-clasico] parse error: {}", e);
                    }
                }
            }
        }

        if should_exit {
            break;
        }

        if let Some(obs) = latest_obs {
            if skipped_kills > 0 {
                if let Some(rt) = runtime.as_mut() {
                    rt.state.our_kills += skipped_kills;
                }
            }
            let tick = obs.tick;
            let (min_p, max_p) = runtime
                .as_ref()
                .map(|r| (r.min_pitch, r.max_pitch))
                .unwrap_or((-0.7, 1.3));

            let action = if let Some(rt) = runtime.as_mut() {
                match panic::catch_unwind(AssertUnwindSafe(|| rt.act(&obs))) {
                    Ok(a) => {
                        if let Some(m) = a.move_dir { last_continuous_move = Some(m); }
                        if let Some(aim) = a.aim { last_continuous_aim = Some(aim); }
                        if let Some(b) = a.boost { last_continuous_boost = Some(b); }
                        a
                    }
                    Err(_) => {
                        error_count += 1;
                        if error_count <= 5 {
                            eprintln!("[el-clasico] panic in act() at tick {}", tick);
                        }
                        Action {
                            tick,
                            move_dir: last_continuous_move,
                            aim: last_continuous_aim,
                            boost: last_continuous_boost,
                            ..Default::default()
                        }
                    }
                }
            } else {
                Action {
                    tick,
                    move_dir: last_continuous_move,
                    aim: last_continuous_aim,
                    boost: last_continuous_boost,
                    ..Default::default()
                }
            };

            let wire = action.to_wire_sanitized(min_p, max_p);
            send_json(&mut out, &wire);
        }

        if should_exit {
            break;
        }
    }
}
