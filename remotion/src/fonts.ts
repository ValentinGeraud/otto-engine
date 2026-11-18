/**
 * Polices des sous-titres et du hook. Les polices Google sont chargées au rendu (même rendu sur le PC
 * et sur le moteur cloud Linux, qui n'a ni Arial Black ni Impact) ; une police système a toujours
 * un sosie Google en secours. Liste alignée sur apps/web/src/lib/clip/styles.ts (CLIP_FONTS).
 */
import { useEffect, useState } from "react";
import { continueRender, delayRender } from "remotion";

const GOOGLE: Record<string, { query: string; weight: number }> = {
  Montserrat: { query: "Montserrat:wght@900", weight: 900 },
  Anton: { query: "Anton", weight: 400 },
  "Bebas Neue": { query: "Bebas+Neue", weight: 400 },
  Poppins: { query: "Poppins:wght@800", weight: 800 },
  "Archivo Black": { query: "Archivo+Black", weight: 400 },
  Bangers: { query: "Bangers", weight: 400 },
  "Luckiest Guy": { query: "Luckiest+Guy", weight: 400 },
  Oswald: { query: "Oswald:wght@700", weight: 700 },
  "Playfair Display": { query: "Playfair+Display:wght@900", weight: 900 },
  "Permanent Marker": { query: "Permanent+Marker", weight: 400 },
};
const LOOKALIKE: Record<string, string> = { "Arial Black": "Archivo Black", Impact: "Anton", Georgia: "Playfair Display", Arial: "Montserrat" };

/** Police Google réellement chargée pour ce nom (elle-même, ou le sosie d'une police système). */
const googleOf = (font: string) => (GOOGLE[font] ? font : LOOKALIKE[font]);

/** `font-family` CSS : la police demandée, puis son sosie Google, puis une police générique. */
export const fontStack = (font: string) => {
  const g = googleOf(font);
  return [`"${font}"`, g && g !== font ? `"${g}"` : null, "sans-serif"].filter(Boolean).join(", ");
};

/** Graisse à utiliser (les polices d'affichage n'existent qu'en 400 : 900 les ferait « faux gras »). */
export const fontWeight = (font: string) => (GOOGLE[font] ? GOOGLE[font].weight : 900);

async function load(font: string) {
  const g = googleOf(font);
  if (!g || typeof document === "undefined") return;
  const href = `https://fonts.googleapis.com/css2?family=${GOOGLE[g].query}&display=block`;
  if (!document.querySelector(`link[href="${href}"]`)) {
    await new Promise<void>((ok) => {
      const l = document.createElement("link");
      l.rel = "stylesheet";
      l.href = href;
      l.onload = () => ok();
      l.onerror = () => ok(); // hors ligne : on garde la police de secours
      document.head.appendChild(l);
    });
  }
  await document.fonts.load(`${GOOGLE[g].weight} 80px "${g}"`);
}

/** Bloque le rendu tant que la police n'est pas prête (8 s maximum, jamais d'échec du rendu). */
export function useClipFont(font: string) {
  const [handle] = useState(() => delayRender(`police ${font}`));
  useEffect(() => {
    let done = false;
    const finish = () => {
      if (!done) {
        done = true;
        continueRender(handle);
      }
    };
    const t = setTimeout(finish, 8000);
    load(font).then(finish, finish);
    return () => clearTimeout(t);
  }, [font, handle]);
}
