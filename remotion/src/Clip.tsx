/**
 * Montage Remotion posé sur le clip « de base » sorti par render.py (déjà coupé, recadré,
 * étalonné, avec le son). Tout ce qui bouge est ajouté ici :
 *   - zoom lent à chaque plan, punch-in + tremblement + flash sur les « boom » ;
 *   - effets liés aux bruitages : basse, scratch (noir et blanc), ka-ching (doré), glitch,
 *     riser (zoom qui monte avant la carte de fin), trombone (échec) ;
 *   - sous-titres mot à mot qui rebondissent, mots-clés en couleur ;
 *   - emojis qui surgissent sur les mots forts et les rires ;
 *   - hook animé, crédit, carte de fin, barre de progression ;
 *   - animations « abonne-toi » et « like » (Engagement.tsx).
 * Le watermark et le texte imposés par une campagne restent fixes (jamais zoomés).
 */
import React from "react";
import {
  AbsoluteFill,
  Audio,
  Img,
  interpolate,
  OffthreadVideo,
  Sequence,
  spring,
  useCurrentFrame,
} from "remotion";
import { Engage, Engagement } from "./Engagement";

type Word = { w: string; s: number; e: number; key?: boolean };
type Fx = { t: number; kind: string; emoji?: string; dur?: number };

export type ClipProps = {
  fps: number;
  total: number;
  body: number;
  video: string;
  font: string;
  colors: { active: string; key: string; hookText: string; hookBack: string; bar: string; card: string };
  sub: { size: number; marginBottom: number; wordsPerLine: number };
  hook: { text: string; seconds: number; size: number } | null;
  credit: string | null;
  card: { label: string; text: string; at: number } | null;
  question: { text: string; at: number } | null;
  words: Word[];
  fx: Fx[];
  cuts: number[];
  sfx: Record<string, string>;
  sfxVolume: number;
  progressBar: boolean;
  intensity: number;
  cta: { text: string; size: number; top?: number; bottom?: number } | null;
  watermark: { src: string; width: number; opacity: number; top?: number; bottom?: number } | null;
  engage: Engage | null;
  style?: string;
};

export const defaultProps: ClipProps = {
  fps: 30, total: 10, body: 10, video: "", font: "Arial Black",
  colors: { active: "#FFE500", key: "#FF5A36", hookText: "#FFFFFF", hookBack: "#000000", bar: "#FFE500", card: "#141414" },
  sub: { size: 82, marginBottom: 560, wordsPerLine: 3 },
  hook: null, credit: null, card: null, question: null, words: [], fx: [], cuts: [], sfx: {},
  sfxVolume: 0.5, progressBar: true, intensity: 1, cta: null, watermark: null, engage: null,
};

const W = 1080;
const H = 1920;
const EMOJI_FONT = "'Segoe UI Emoji','Apple Color Emoji','Noto Color Emoji',sans-serif";

// ---------- caméra ----------
function useCamera(p: ClipProps) {
  const frame = useCurrentFrame();
  const t = frame / p.fps;
  const k = p.intensity;
  // zoom lent qui repart à chaque coupe : l'image ne reste jamais figée
  const prevCut = [0, ...p.cuts].filter((c) => c <= t).pop() ?? 0;
  const nextCut = [...p.cuts, p.body].find((c) => c > t) ?? p.body;
  const shotIdx = p.cuts.filter((c) => c <= t).length;
  const prog = t < p.body ? (t - prevCut) / Math.max(nextCut - prevCut, 0.3) : 1;
  let scale = 1.04 + 0.045 * k * Math.min(Math.max(prog, 0), 1);
  let x = (shotIdx % 2 ? 1 : -1) * 14 * k * (prog - 0.5); // glissement, sens alterné
  let y = 0;
  let rot = 0;
  let filter = "";
  const sinceCut = t - prevCut;
  if (prevCut > 0 && sinceCut < 0.25) scale += 0.03 * k * (1 - sinceCut / 0.25); // bump sur la coupe
  for (const f of p.fx) {
    const dt = t - f.t;
    if (f.kind === "riser" || f.kind === "reverse") {
      const dur = f.dur ?? 1.1;
      if (dt >= 0 && dt <= dur) {
        const r = dt / dur;
        scale += (f.kind === "reverse" ? 0.07 : 0.12) * k * r * r;
        if (f.kind === "riser") x += Math.sin(dt * 70) * 6 * k * r;
      }
      continue;
    }
    if (dt < 0 || dt > 1.2) continue;
    if (f.kind === "boom" || f.kind === "bass") {
      const s = spring({ frame: frame - Math.round(f.t * p.fps), fps: p.fps, config: { damping: 9, stiffness: 180 } });
      scale += (f.kind === "bass" ? 0.28 : 0.2) * k * Math.exp(-dt * 3.2) * s;
      const shake = Math.exp(-dt * 9) * 22 * k;
      x += Math.sin(dt * 95) * shake;
      y += Math.cos(dt * 77) * shake;
      rot += Math.sin(dt * 60) * Math.exp(-dt * 9) * 1.6 * k;
    } else if (f.kind === "hit") {
      // impact « cinéma » : punch-in net, petite secousse, léger contraste
      const s = spring({ frame: frame - Math.round(f.t * p.fps), fps: p.fps, config: { damping: 14, stiffness: 260 } });
      scale += 0.13 * k * Math.exp(-dt * 2.6) * s;
      const shake = Math.exp(-dt * 14) * 9 * k;
      x += Math.sin(dt * 90) * shake;
      y += Math.cos(dt * 70) * shake;
      if (dt < 0.3) filter = `contrast(${1 + 0.25 * (1 - dt / 0.3)}) saturate(${1 + 0.2 * (1 - dt / 0.3)})`;
    } else if (f.kind === "boing" && dt < 0.6) {
      y += Math.sin(dt * 30) * Math.exp(-dt * 6) * 26 * k;
      scale += 0.04 * k * Math.exp(-dt * 6);
    } else if (f.kind === "scratch" && dt < 0.7) {
      scale += 0.14 * k;
      rot += -2.5 * k;
      filter = "grayscale(1) contrast(1.25)";
    } else if (f.kind === "fail" && dt < 1.1) {
      scale -= 0.03 * k * Math.min(dt * 4, 1);
      filter = `grayscale(${Math.min(dt * 3, 0.9)})`;
    } else if (f.kind === "kaching" && dt < 0.5) {
      const e = Math.exp(-dt * 5);
      scale += 0.06 * k * e;
      filter = `sepia(${0.6 * e}) saturate(${1 + 1.2 * e}) brightness(${1 + 0.15 * e})`;
    } else if (f.kind === "glitch" && dt < 0.35) {
      const j = Math.floor(dt * 30);
      x += (((j * 37) % 11) - 5) * 6 * k;
      filter = `hue-rotate(${(j * 73) % 360}deg) saturate(2)`;
    } else if (f.kind === "swish" && dt < 0.2) {
      x += (1 - dt / 0.2) * 40 * k * (Math.round(f.t * 10) % 2 ? 1 : -1);
    }
  }
  return { scale, x, y, rot, filter };
}

// assombrit les bords sur les impacts : le regard va au centre
const Vignette: React.FC<{ fx: Fx[]; fps: number }> = ({ fx, fps }) => {
  const frame = useCurrentFrame();
  let o = 0;
  for (const f of fx) {
    if (f.kind !== "hit") continue;
    const d = (frame - Math.round(f.t * fps)) / fps;
    if (d >= 0 && d < 0.8) o = Math.max(o, 0.75 * Math.exp(-d * 4));
  }
  return o ? (
    <AbsoluteFill style={{ background: "radial-gradient(ellipse at 50% 45%, transparent 35%, rgba(0,0,0,.95) 100%)", opacity: o }} />
  ) : null;
};

const Flash: React.FC<{ fx: Fx[]; fps: number }> = ({ fx, fps }) => {
  const frame = useCurrentFrame();
  let o = 0;
  for (const f of fx) {
    if (f.kind !== "boom" && f.kind !== "bass") continue;
    const d = frame - Math.round(f.t * fps);
    if (d >= 0 && d < 5) o = Math.max(o, interpolate(d, [0, 5], [f.kind === "bass" ? 0.7 : 0.5, 0]));
  }
  return o ? <AbsoluteFill style={{ background: "white", opacity: o }} /> : null;
};

// ---------- sous-titres ----------
function chunks(words: Word[], n: number) {
  const out: Word[][] = [];
  let cur: Word[] = [];
  for (const w of words) {
    if (/^\[[^\]]*\]$/.test(w.w)) continue;
    if (cur.length && (cur.length >= n || w.s - cur[cur.length - 1].e > 0.6)) {
      out.push(cur);
      cur = [];
    }
    cur.push(w);
  }
  if (cur.length) out.push(cur);
  return out;
}

const stroke = (px: number) =>
  `${px}px ${px}px 0 #000, -${px}px ${px}px 0 #000, ${px}px -${px}px 0 #000, -${px}px -${px}px 0 #000, 0 ${px * 1.6}px ${px * 2}px rgba(0,0,0,.6)`;

const Captions: React.FC<{ p: ClipProps }> = ({ p }) => {
  const frame = useCurrentFrame();
  const t = frame / p.fps;
  const end = p.card ? p.card.at : p.body;
  if (t >= end) return null;
  const cs = chunks(p.words, p.sub.wordsPerLine);
  const ci = cs.findIndex((c, i) => t >= c[0].s && t < (cs[i + 1]?.[0].s ?? c[c.length - 1].e + 0.4));
  if (ci < 0) return null;
  const c = cs[ci];
  const enter = spring({ frame: frame - Math.round(c[0].s * p.fps), fps: p.fps, config: { damping: 12, stiffness: 220 } });
  return (
    <AbsoluteFill style={{ justifyContent: "flex-end", alignItems: "center", paddingBottom: p.sub.marginBottom }}>
      <div
        style={{
          fontFamily: p.font, fontSize: p.sub.size, fontWeight: 900, color: "white", textAlign: "center",
          lineHeight: 1.1, maxWidth: W - 140, textShadow: stroke(5),
          transform: `scale(${interpolate(enter, [0, 1], [0.6, 1])}) translateY(${interpolate(enter, [0, 1], [40, 0])}px)`,
        }}
      >
        {c.map((w, i) => {
          const active = t >= w.s && t < (c[i + 1]?.s ?? w.e + 0.4);
          const pop = active
            ? spring({ frame: frame - Math.round(w.s * p.fps), fps: p.fps, config: { damping: 10, stiffness: 300 } })
            : 0;
          const color = w.key ? p.colors.key : active ? p.colors.active : "white";
          return (
            <span
              key={i}
              style={{
                display: "inline-block", margin: "0 16px", color,
                transform: `scale(${1 + 0.16 * pop}) rotate(${active && w.key ? -3 * pop : 0}deg)`,
              }}
            >
              {w.w.toUpperCase()}
            </span>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

// ---------- emojis ----------
const Emojis: React.FC<{ p: ClipProps }> = ({ p }) => {
  const frame = useCurrentFrame();
  const items = p.fx.filter((f) => f.emoji);
  return (
    <>
      {items.map((f, i) => {
        const d = frame - Math.round(f.t * p.fps);
        const len = Math.round(1.0 * p.fps);
        if (d < 0 || d > len) return null;
        const s = spring({ frame: d, fps: p.fps, config: { damping: 8, stiffness: 200 } });
        const out = interpolate(d, [len - 6, len], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
        const side = i % 2 ? 1 : -1;
        return (
          <div
            key={i}
            style={{
              position: "absolute", left: W / 2 + side * 250 - 90, top: H - p.sub.marginBottom - 380,
              fontSize: 180, lineHeight: 1, opacity: out, fontFamily: EMOJI_FONT,
              transform: `scale(${s}) rotate(${side * (12 - 12 * s) + Math.sin(d / 3) * 6}deg) translateY(${-d * 1.5}px)`,
              filter: "drop-shadow(0 8px 12px rgba(0,0,0,.5))",
            }}
          >
            {f.emoji}
          </div>
        );
      })}
    </>
  );
};

// ---------- hook, crédit, carte de fin, barre ----------
const Hook: React.FC<{ p: ClipProps }> = ({ p }) => {
  const frame = useCurrentFrame();
  if (!p.hook || !p.hook.text) return null;
  const endF = Math.round(p.hook.seconds * p.fps);
  if (frame > endF) return null;
  const s = spring({ frame, fps: p.fps, config: { damping: 10, stiffness: 160 } });
  const out = interpolate(frame, [endF - 6, endF], [1, 0], { extrapolateLeft: "clamp" });
  const wiggle = Math.sin(frame / 4) * 1.2 * Math.exp(-frame / 40);
  return (
    <AbsoluteFill style={{ alignItems: "center", paddingTop: 260 }}>
      <div
        style={{
          fontFamily: p.font, fontSize: p.hook.size, fontWeight: 900, color: p.colors.hookText,
          background: p.colors.hookBack, padding: "18px 30px", borderRadius: 18, maxWidth: W - 180,
          textAlign: "center", lineHeight: 1.15, opacity: out, boxShadow: "0 10px 30px rgba(0,0,0,.35)",
          transform: `scale(${interpolate(s, [0, 1], [0.4, 1])}) rotate(${wiggle - 2 * (1 - s)}deg)`,
        }}
      >
        {p.hook.text}
      </div>
    </AbsoluteFill>
  );
};

const Credit: React.FC<{ p: ClipProps }> = ({ p }) => {
  const frame = useCurrentFrame();
  if (!p.credit) return null;
  const o = interpolate(frame, [0, 6, 3.6 * p.fps, 4 * p.fps], [0, 0.85, 0.85, 0], { extrapolateRight: "clamp" });
  return (
    <AbsoluteFill style={{ alignItems: "center", paddingTop: 150 }}>
      <div style={{ fontFamily: "Arial", fontWeight: 700, fontSize: 34, color: "white", opacity: o, textShadow: stroke(2) }}>
        {p.credit}
      </div>
    </AbsoluteFill>
  );
};

const CardBox: React.FC<{ at: number; fps: number; size: number; font: string; bg: string; children: React.ReactNode }> = ({
  at, fps, size, font, bg, children,
}) => {
  const frame = useCurrentFrame();
  if (frame < Math.round(at * fps)) return null;
  const s = spring({ frame: frame - Math.round(at * fps), fps, config: { damping: 9, stiffness: 170 } });
  return (
    <div
      style={{
        fontFamily: font, fontWeight: 900, fontSize: size, color: "white", background: bg,
        padding: "26px 36px", borderRadius: 26, textAlign: "center", lineHeight: 1.15, maxWidth: W - 200,
        boxShadow: "0 14px 40px rgba(0,0,0,.5)",
        transform: `scale(${interpolate(s, [0, 1], [0.3, 1])}) rotate(${(1 - s) * -8}deg)`,
      }}
    >
      {children}
    </div>
  );
};

const EndCard: React.FC<{ p: ClipProps }> = ({ p }) => {
  const hs = p.hook?.size ?? 66;
  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center", gap: 50 }}>
      {p.card && (
        <CardBox at={p.card.at} fps={p.fps} size={Math.round(hs * 1.1)} font={p.font} bg={p.colors.card}>
          <div style={{ color: p.colors.active }}>{p.card.label}</div>
          <div>{p.card.text}</div>
        </CardBox>
      )}
      {p.question && (
        <CardBox at={p.question.at} fps={p.fps} size={Math.round(hs * 0.95)} font={p.font} bg={p.colors.card}>
          <span style={{ color: p.colors.active }}>{p.question.text}</span>
        </CardBox>
      )}
    </AbsoluteFill>
  );
};

const Progress: React.FC<{ p: ClipProps }> = ({ p }) => {
  const frame = useCurrentFrame();
  if (!p.progressBar) return null;
  const w = Math.min(frame / p.fps / p.body, 1) * W;
  return <div style={{ position: "absolute", left: 0, top: 0, height: 12, width: w, background: p.colors.bar }} />;
};

// ---------- éléments imposés par une campagne (fixes) ----------
const Campaign: React.FC<{ p: ClipProps }> = ({ p }) => (
  <>
    {p.cta && (
      <div
        style={{
          position: "absolute", left: 0, right: 0, textAlign: "center",
          ...(p.cta.top !== undefined ? { top: p.cta.top } : { bottom: p.cta.bottom ?? 420 }),
        }}
      >
        <span
          style={{
            fontFamily: "Arial", fontWeight: 700, fontSize: p.cta.size, color: "white",
            background: "rgba(0,0,0,.6)", padding: "10px 18px",
          }}
        >
          {p.cta.text}
        </span>
      </div>
    )}
    {p.watermark && (
      <Img
        src={p.watermark.src}
        style={{
          position: "absolute", width: p.watermark.width, left: (W - p.watermark.width) / 2,
          opacity: p.watermark.opacity,
          ...(p.watermark.top !== undefined ? { top: p.watermark.top } : { bottom: p.watermark.bottom ?? 380 }),
        }}
      />
    )}
  </>
);

// ---------- bruitages ----------
const Sounds: React.FC<{ p: ClipProps }> = ({ p }) => (
  <>
    {p.fx.map((f, i) =>
      p.sfx[f.kind] ? (
        <Sequence key={i} from={Math.round(f.t * p.fps)} layout="none">
          <Audio src={p.sfx[f.kind]} volume={p.sfxVolume} />
        </Sequence>
      ) : null,
    )}
  </>
);

export const Clip: React.FC<ClipProps> = (p) => {
  const cam = useCamera(p);
  return (
    <AbsoluteFill style={{ background: "black" }}>
      <AbsoluteFill
        style={{
          transform: `translate(${cam.x}px, ${cam.y}px) scale(${cam.scale}) rotate(${cam.rot}deg)`,
          transformOrigin: "50% 42%",
          filter: cam.filter || undefined,
        }}
      >
        <OffthreadVideo src={p.video} style={{ width: W, height: H }} />
      </AbsoluteFill>
      <Flash fx={p.fx} fps={p.fps} />
      <Vignette fx={p.fx} fps={p.fps} />
      <Credit p={p} />
      <Hook p={p} />
      <Captions p={p} />
      <Emojis p={p} />
      <EndCard p={p} />
      <Progress p={p} />
      <Engagement e={p.engage} fps={p.fps} sfx={p.sfx} volume={p.sfxVolume} funny={(p.style ?? "funny") === "funny"} />
      <Campaign p={p} />
      <Sounds p={p} />
    </AbsoluteFill>
  );
};
