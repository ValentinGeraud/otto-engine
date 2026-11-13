// Animations « abonne-toi » (bouton rouge + curseur qui clique + cloche) et « like » (cœur + pouces).
import React from "react";
import { Audio, interpolate, Sequence, spring, useCurrentFrame } from "remotion";

export type Engage = {
  subscribe: { at: number; label: string; done: string } | null;
  like: { at: number } | null;
};

const W = 1080;
const H = 1920;
const EMOJI_FONT = "'Segoe UI Emoji','Apple Color Emoji','Noto Color Emoji',sans-serif";

const Cursor: React.FC<{ press: number }> = ({ press }) => (
  <svg
    width="90"
    height="90"
    viewBox="0 0 24 24"
    style={{ transform: `scale(${1 - 0.18 * press})`, filter: "drop-shadow(0 4px 6px rgba(0,0,0,.6))" }}
  >
    <path
      d="M9 2v11.2l-2.1-2.1a1.6 1.6 0 0 0-2.3 2.3l5.2 5.6c.8.9 2 1.4 3.2 1.4H16a4 4 0 0 0 4-4v-5.2a1.6 1.6 0 0 0-3.2 0V10a1.6 1.6 0 0 0-3.2 0v-.6a1.6 1.6 0 0 0-3.2 0V2a1.6 1.6 0 0 0-3.2 0Z"
      fill="white"
      stroke="black"
      strokeWidth="1.1"
    />
  </svg>
);

const Subscribe: React.FC<{ e: NonNullable<Engage["subscribe"]>; fps: number }> = ({ e, fps }) => {
  const frame = useCurrentFrame();
  const d = frame - Math.round(e.at * fps);
  const len = Math.round(2.6 * fps);
  if (d < 0 || d > len) return null;
  const enter = spring({ frame: d, fps, config: { damping: 11, stiffness: 170 } });
  const clickF = Math.round(1.0 * fps);
  const clicked = d >= clickF;
  const press = d >= clickF - 3 && d < clickF + 3 ? 1 : 0;
  const after = clicked ? spring({ frame: d - clickF, fps, config: { damping: 8, stiffness: 220 } }) : 0;
  const out = interpolate(d, [len - 7, len], [1, 0], { extrapolateLeft: "clamp" });
  const cursorX = interpolate(d, [0, clickF - 4], [300, 150], { extrapolateRight: "clamp" });
  const cursorY = interpolate(d, [0, clickF - 4], [170, 50], { extrapolateRight: "clamp" });
  const bell = clicked ? Math.sin((d - clickF) / 1.6) * 22 * Math.exp(-(d - clickF) / 14) : 0;
  const scale = interpolate(enter, [0, 1], [0.2, 1]) * (1 - 0.06 * press) + 0.1 * after * (1 - after);
  return (
    <div style={{ position: "absolute", top: 330, left: 0, right: 0, display: "flex", justifyContent: "center", opacity: out }}>
      <div style={{ position: "relative", transform: `scale(${scale})`, display: "flex", alignItems: "center", gap: 22 }}>
        <div
          style={{
            fontFamily: "Arial Black, Arial", fontWeight: 900, fontSize: 56, padding: "20px 44px", borderRadius: 60,
            background: clicked ? "#3a3a3a" : "#FF0033", color: "white", boxShadow: "0 10px 30px rgba(0,0,0,.45)",
            whiteSpace: "nowrap",
          }}
        >
          {clicked ? `${e.done} ✓` : e.label}
        </div>
        {clicked && (
          <div style={{ fontSize: 80, transform: `rotate(${bell}deg)`, transformOrigin: "50% 10%", fontFamily: EMOJI_FONT }}>
            🔔
          </div>
        )}
        <div style={{ position: "absolute", left: cursorX, top: cursorY }}>
          <Cursor press={press} />
        </div>
      </div>
    </div>
  );
};

const Like: React.FC<{ at: number; fps: number }> = ({ at, fps }) => {
  const frame = useCurrentFrame();
  const d = frame - Math.round(at * fps);
  const len = Math.round(1.6 * fps);
  if (d < 0 || d > len) return null;
  const s = spring({ frame: d, fps, config: { damping: 7, stiffness: 200 } });
  const out = interpolate(d, [len - 8, len], [1, 0], { extrapolateLeft: "clamp" });
  const r = interpolate(d, [2, len], [40, 300], { extrapolateLeft: "clamp" });
  const o = interpolate(d, [2, 6, len], [0, 1, 0], { extrapolateLeft: "clamp" });
  return (
    <div style={{ position: "absolute", left: W - 230, top: H * 0.42, opacity: out }}>
      {Array.from({ length: 9 }, (_, i) => {
        const a = (i / 9) * Math.PI * 2 + 0.3;
        return (
          <div
            key={i}
            style={{ position: "absolute", left: Math.cos(a) * r - 30, top: Math.sin(a) * r - 30 - d * 2, fontSize: 60, opacity: o, fontFamily: EMOJI_FONT }}
          >
            {i % 3 === 0 ? "👍" : "❤️"}
          </div>
        );
      })}
      <div
        style={{
          position: "absolute", left: -90, top: -90, fontSize: 180, fontFamily: EMOJI_FONT,
          transform: `scale(${s}) rotate(${(1 - s) * -25}deg)`, filter: "drop-shadow(0 10px 18px rgba(0,0,0,.5))",
        }}
      >
        ❤️
      </div>
    </div>
  );
};

export const Engagement: React.FC<{ e: Engage | null; fps: number; sfx: Record<string, string>; volume: number; funny?: boolean }> = ({
  e, fps, sfx, volume, funny = true,
}) => {
  if (!e) return null;
  const snd = (at: number, name: string, key: string) =>
    sfx[name] ? (
      <Sequence key={key} from={Math.round(at * fps)} layout="none">
        <Audio src={sfx[name]} volume={volume} />
      </Sequence>
    ) : null;
  return (
    <>
      {e.subscribe && <Subscribe e={e.subscribe} fps={fps} />}
      {e.like && <Like at={e.like.at} fps={fps} />}
      {e.subscribe && snd(e.subscribe.at, "swish", "s0")}
      {e.subscribe && snd(e.subscribe.at + 1.0, "pop", "s1")}
      {e.subscribe && snd(e.subscribe.at + 1.08, "ding", "s2")}
      {e.like && snd(e.like.at, funny ? "boing" : "swish", "l0")}
    </>
  );
};
