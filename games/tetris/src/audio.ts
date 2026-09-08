/**
 * Sound effects and background music using Web Audio API (no external files).
 * Chiptune-style BGM and classic Tetris-inspired SFX.
 * @module audio
 */

let audioContext: AudioContext | null = null;

function getContext(): AudioContext {
  if (!audioContext)
    audioContext = new (window.AudioContext ||
      (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext)();
  return audioContext;
}

// --- Background music (looping chiptune melody + bass) ---

const BGM_VOLUME = 0.14;
const BPM = 118;
const BEAT_SEC = 60 / BPM;

/** Melody: [freq Hz, duration beats]. */
const BGM_MELODY: [number, number][] = [
  [330, 0.5], [392, 0.5], [523, 0.5], [392, 0.5],
  [330, 0.5], [392, 0.5], [523, 0.5], [392, 0.5],
  [262, 0.5], [330, 0.5], [392, 0.5], [330, 0.5],
  [349, 1], [392, 0.5], [392, 0.5],
  [330, 0.5], [392, 0.5], [523, 0.5], [392, 0.5],
  [330, 0.5], [392, 0.5], [523, 0.5], [659, 0.5],
  [523, 1], [392, 1], [330, 1], [262, 1],
];

/** Bass: [freq Hz, duration beats] — root/fifth under melody. */
const BGM_BASS: [number, number][] = [
  [131, 2], [98, 2], [131, 2], [117, 2],
  [131, 2], [98, 2], [131, 2], [98, 2],
];

let bgmGain: GainNode | null = null;
let bgmScheduledUntil = 0;
let bgmTimeoutId: ReturnType<typeof setTimeout> | null = null;

function scheduleBGMChunk(startTime: number): void {
  const ctx = getContext();
  if (!bgmGain) return;
  let t = startTime;

  // Melody (square + triangle blend for softer chiptune)
  for (const [freq, beats] of BGM_MELODY) {
    const duration = beats * BEAT_SEC;
    const osc = ctx.createOscillator();
    const tri = ctx.createOscillator();
    const env = ctx.createGain();
    const mix = ctx.createGain();
    osc.type = 'square';
    tri.type = 'triangle';
    osc.frequency.setValueAtTime(freq, t);
    tri.frequency.setValueAtTime(freq, t);
    mix.gain.value = 0.5;
    osc.connect(mix);
    tri.connect(mix);
    mix.connect(env);
    env.connect(bgmGain);
    env.gain.setValueAtTime(0, t);
    env.gain.linearRampToValueAtTime(BGM_VOLUME * 0.4, t + 0.02);
    env.gain.setValueAtTime(BGM_VOLUME * 0.4, t + duration - 0.04);
    env.gain.linearRampToValueAtTime(0.001, t + duration);
    osc.start(t);
    osc.stop(t + duration);
    tri.start(t);
    tri.stop(t + duration);
    t = Math.max(t, t + duration);
  }

  const melodyEnd = t;
  t = startTime;

  // Bass (triangle, slower pattern)
  for (const [freq, beats] of BGM_BASS) {
    const duration = beats * BEAT_SEC;
    const osc = ctx.createOscillator();
    const env = ctx.createGain();
    osc.type = 'triangle';
    osc.frequency.setValueAtTime(freq, t);
    env.gain.setValueAtTime(0, t);
    env.gain.linearRampToValueAtTime(BGM_VOLUME * 0.25, t + 0.03);
    env.gain.setValueAtTime(BGM_VOLUME * 0.25, t + duration - 0.05);
    env.gain.linearRampToValueAtTime(0.001, t + duration);
    osc.connect(env);
    env.connect(bgmGain);
    osc.start(t);
    osc.stop(t + duration);
    t += duration;
  }

  const bassEnd = t;
  bgmScheduledUntil = Math.max(melodyEnd, bassEnd);
}

function scheduleBGMLoop(): void {
  if (!bgmGain) return;
  const ctx = getContext();
  const now = ctx.currentTime;
  if (now >= bgmScheduledUntil) scheduleBGMChunk(now);
  bgmTimeoutId = setTimeout(
    scheduleBGMLoop,
    Math.max(100, (bgmScheduledUntil - now) * 1000 - 500)
  );
}

/** Start looping background music (e.g. when game starts). */
export function startBGM(): void {
  try {
    const ctx = getContext();
    if (bgmGain) return;
    bgmGain = ctx.createGain();
    bgmGain.gain.value = BGM_VOLUME;
    bgmGain.connect(ctx.destination);
    bgmScheduledUntil = 0;
    scheduleBGMLoop();
  } catch {
    // Ignore
  }
}

/** Stop background music and clear scheduled notes. */
export function stopBGM(): void {
  if (bgmTimeoutId !== null) {
    clearTimeout(bgmTimeoutId);
    bgmTimeoutId = null;
  }
  if (bgmGain) {
    try {
      bgmGain.disconnect();
    } catch {
      // already disconnected
    }
    bgmGain = null;
  }
  bgmScheduledUntil = 0;
}

/** Mute/unmute BGM (e.g. when game is paused). */
export function setBGMVolume(volume: number): void {
  if (bgmGain) bgmGain.gain.setValueAtTime(Math.max(0, volume), getContext().currentTime);
}

// --- Sound effects ---

function playTone(
  freq: number,
  duration: number,
  type: OscillatorType = 'sine',
  volume = 0.12,
  rampDown = true
): void {
  try {
    const ctx = getContext();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, ctx.currentTime);
    gain.gain.setValueAtTime(volume, ctx.currentTime);
    if (rampDown) gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + duration);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + duration);
  } catch {
    // Ignore
  }
}

/** Classic Tetris-style line clear: arpeggio that scales with number of lines (1–4). */
export function playLineClear(lines: number): void {
  try {
    const ctx = getContext();
    const t0 = ctx.currentTime;
    // Softer arpeggio: triangle wave, lower volume, gentle attack
    const baseFreqs = [523, 784, 1047, 784]; // C5 G5 C6 G5 (slightly lower)
    const count = Math.min(4, Math.max(1, lines));
    const step = 0.07 - count * 0.008;
    const sustain = 0.05 + count * 0.015;
    const vol = 0.055;

    for (let i = 0; i < count; i++) {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'triangle';
      osc.frequency.setValueAtTime(baseFreqs[i % 4], t0 + i * step);
      gain.gain.setValueAtTime(0, t0 + i * step);
      gain.gain.linearRampToValueAtTime(vol, t0 + i * step + 0.03);
      gain.gain.setValueAtTime(vol, t0 + i * step + sustain);
      gain.gain.linearRampToValueAtTime(0.001, t0 + i * step + sustain + 0.1);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(t0 + i * step);
      osc.stop(t0 + i * step + sustain + 0.12);
    }
    // Soft low note on last step
    const lastT = t0 + (count - 1) * step + sustain * 0.4;
    const low = ctx.createOscillator();
    const lowG = ctx.createGain();
    low.type = 'sine';
    low.frequency.setValueAtTime(196, lastT);
    lowG.gain.setValueAtTime(0, lastT);
    lowG.gain.linearRampToValueAtTime(0.04, lastT + 0.04);
    lowG.gain.linearRampToValueAtTime(0.001, lastT + 0.15);
    low.connect(lowG);
    lowG.connect(ctx.destination);
    low.start(lastT);
    low.stop(lastT + 0.15);
  } catch {
    // Ignore
  }
}

/** Punchy hard drop: low thud + short tone. */
export function playHardDrop(): void {
  try {
    const ctx = getContext();
    const t = ctx.currentTime;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.setValueAtTime(80, t);
    osc.frequency.exponentialRampToValueAtTime(40, t + 0.06);
    gain.gain.setValueAtTime(0.2, t);
    gain.gain.exponentialRampToValueAtTime(0.001, t + 0.08);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start(t);
    osc.stop(t + 0.08);
    // Soft click
    const click = ctx.createOscillator();
    const clickG = ctx.createGain();
    click.type = 'square';
    click.frequency.setValueAtTime(200, t);
    clickG.gain.setValueAtTime(0.04, t);
    clickG.gain.exponentialRampToValueAtTime(0.001, t + 0.03);
    click.connect(clickG);
    clickG.connect(ctx.destination);
    click.start(t);
    click.stop(t + 0.03);
  } catch {
    // Ignore
  }
}

/** Soft tick for move left/right. */
export function playMove(): void {
  playTone(280, 0.03, 'sine', 0.05);
}

/** Short blip for rotate. */
export function playRotate(): void {
  playTone(400, 0.04, 'square', 0.06);
}

/** Very soft tick for soft drop. */
export function playSoftDrop(): void {
  playTone(200, 0.025, 'sine', 0.035);
}

/** Satisfying “plunk” when piece locks. */
export function playLock(): void {
  try {
    const ctx = getContext();
    const t = ctx.currentTime;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'triangle';
    osc.frequency.setValueAtTime(150, t);
    osc.frequency.exponentialRampToValueAtTime(80, t + 0.1);
    gain.gain.setValueAtTime(0.12, t);
    gain.gain.exponentialRampToValueAtTime(0.001, t + 0.12);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start(t);
    osc.stop(t + 0.12);
  } catch {
    // Ignore
  }
}

/** Quick swish for hold. */
export function playHold(): void {
  playTone(350, 0.05, 'sine', 0.06);
}

/** Descending “game over” phrase. */
export function playGameOver(): void {
  try {
    const ctx = getContext();
    const t0 = ctx.currentTime;
    const freqs = [392, 349, 330, 262];
    freqs.forEach((freq, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(freq, t0 + i * 0.12);
      gain.gain.setValueAtTime(0.1, t0 + i * 0.12);
      gain.gain.exponentialRampToValueAtTime(0.001, t0 + i * 0.12 + 0.2);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(t0 + i * 0.12);
      osc.stop(t0 + i * 0.12 + 0.22);
    });
  } catch {
    // Ignore
  }
}
