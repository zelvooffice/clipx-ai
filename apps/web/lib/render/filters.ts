/** ffmpeg argument builders for the in-browser renderer (1080x1920, 30 fps, H.264 + AAC, faststart). */
import type { EditPlan } from "./plan";
import { zoomExpr } from "./plan";

export const W = 1080, H = 1920;
const even = (x: number) => Math.max(2, Math.round(x / 2) * 2);
const f3 = (x: number) => x.toFixed(3);

export const ENCODE = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-maxrate", "6M", "-bufsize", "12M", "-r", "30",
  "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart"];

export interface ClipRender {
  srcW: number; srcH: number; duration: number; // duration of the output (after trimming)
  layout: "fit" | "crop"; cropX: number | null; plan: EditPlan; hasAudio: boolean; subtitles: boolean;
}

function zoompan(expr: string, w: number, h: number) {
  return expr === "1" ? "" : `,zoompan=z='${expr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=${w}x${h}:fps=30`;
}

/** filter_complex for one clip: blurred-background "fit" or a full-height "crop" (manual x), punch-ins, subtitles, fades, loudness. */
export function clipFilter(o: ClipRender): string {
  const z = zoomExpr(o.plan, o.duration);
  const fadeOut = Math.max(0, o.duration - o.plan.fade_out);
  const tail = `${o.subtitles ? ",ass=subs.ass:fontsdir=/fonts" : ""},fade=t=in:st=0:d=${o.plan.fade_in},fade=t=out:st=${f3(fadeOut)}:d=${o.plan.fade_out},format=yuv420p[v]`;
  const scaledW = (o.srcW * H) / o.srcH;
  let v: string;
  if (o.layout === "crop" && scaledW >= W) {
    const x = o.cropX === null ? "(iw-ow)/2" : `${f3(Math.min(1, Math.max(0, o.cropX)))}*(iw-ow)`;
    v = `[0:v]fps=30,scale=-2:${H},crop=${W}:${H}:${x}:0,setsar=1${zoompan(z, W, H)}${tail}`;
  } else {
    const wide = o.srcW / o.srcH >= W / H;
    const fw = wide ? W : even((H * o.srcW) / o.srcH), fh = wide ? even((W * o.srcH) / o.srcW) : H;
    v = `[0:v]fps=30,split[a][b];` +
      `[a]scale=${W}:${H}:force_original_aspect_ratio=increase,crop=${W}:${H},scale=135:240,boxblur=4:1,scale=${W}:${H},eq=brightness=-0.25,setsar=1[bg];` +
      `[b]scale=${fw}:${fh},setsar=1${zoompan(z, fw, fh)}[fg];` +
      `[bg][fg]overlay=(W-w)/2:(H-h)/2${tail}`;
  }
  const a = o.hasAudio
    ? `[0:a]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000,afade=t=in:d=${o.plan.fade_in},afade=t=out:st=${f3(fadeOut)}:d=${o.plan.fade_out}[a]`
    : `anullsrc=r=48000:cl=stereo,atrim=duration=${f3(o.duration)}[a]`;
  return `${v};${a}`;
}

export function clipArgs(o: ClipRender & { trimStart: number }): string[] {
  return ["-ss", f3(o.trimStart), "-t", f3(o.duration), "-i", "in.mp4", "-filter_complex", clipFilter(o),
    "-map", "[v]", "-map", "[a]", ...ENCODE, "-t", f3(o.duration), "out.mp4"];
}

// ------------------------------------------------------------------------------------- viral templates
/** POV: an already rendered clip with a banner (ASS overlay). */
export function povArgs(duration: number): string[] {
  return ["-i", "in0.mp4", "-filter_complex", `[0:v]ass=banner.ass:fontsdir=/fonts,format=yuv420p[v]`, "-map", "[v]", "-map", "0:a?",
    ...ENCODE, "-t", f3(duration), "out.mp4"];
}

/** Compilation: rendered clips back to back (each trimmed to `each` seconds), numbered via the banner overlay. */
export function compilationArgs(durations: number[], each: number): string[] {
  const inputs = durations.flatMap((_, i) => ["-i", `in${i}.mp4`]);
  const parts = durations.map((d, i) => {
    const t = f3(Math.min(d, each));
    return `[${i}:v]trim=0:${t},setpts=PTS-STARTPTS,fps=30,format=yuv420p[v${i}];[${i}:a]atrim=0:${t},asetpts=PTS-STARTPTS,aresample=48000[a${i}]`;
  });
  const cat = durations.map((_, i) => `[v${i}][a${i}]`).join("") + `concat=n=${durations.length}:v=1:a=1[cv][a]`;
  return [...inputs, "-filter_complex", `${parts.join(";")};${cat};[cv]ass=banner.ass:fontsdir=/fonts,format=yuv420p[v]`,
    "-map", "[v]", "-map", "[a]", ...ENCODE, "out.mp4"];
}

/** "How me and bro look": the centre halves of two rendered clips stacked, shared caption in the middle. */
export function stackArgs(duration: number): string[] {
  const half = `scale=${W}:${H},crop=${W}:${H / 2}:0:${H / 4},setsar=1`;
  return ["-i", "in0.mp4", "-i", "in1.mp4", "-filter_complex",
    `[0:v]${half}[t];[1:v]${half}[b];[t][b]vstack,ass=banner.ass:fontsdir=/fonts,format=yuv420p[v];[0:a][1:a]amix=inputs=2:duration=shortest:normalize=1[a]`,
    "-map", "[v]", "-map", "[a]", ...ENCODE, "-t", f3(duration), "out.mp4"];
}
