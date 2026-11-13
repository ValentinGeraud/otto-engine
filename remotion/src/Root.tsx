import React from "react";
import { Composition } from "remotion";
import { Clip, ClipProps, defaultProps } from "./Clip";

export const Root: React.FC = () => (
  <Composition
    id="Clip"
    component={Clip as unknown as React.FC<Record<string, unknown>>}
    width={1080}
    height={1920}
    fps={30}
    durationInFrames={300}
    defaultProps={defaultProps as unknown as Record<string, unknown>}
    calculateMetadata={({ props }) => {
      const p = props as unknown as ClipProps;
      return { durationInFrames: Math.max(1, Math.round(p.total * p.fps)), fps: p.fps };
    }}
  />
);
