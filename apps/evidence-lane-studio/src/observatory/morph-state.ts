export type Point3 = [number, number, number];
export type CubicPoints = [Point3, Point3, Point3, Point3];
export type MorphPose = {nodes: Point3[]; nodeOpacity: number[]; curves: CubicPoints[]; edgeOpacity: number[]};

export const smoothProgress = (progress: number) => {
  const p = Math.max(0, Math.min(1, progress));
  return p * p * (3 - 2 * p);
};

/** Capture the displayed pose before a new destination replaces an interrupted morph. */
export function interpolatePose(from: MorphPose, to: MorphPose, blend: number): MorphPose {
  const lerp = (a: number, b: number) => a + (b - a) * blend;
  const point = (a: Point3, b: Point3): Point3 => [lerp(a[0], b[0]), lerp(a[1], b[1]), lerp(a[2], b[2])];
  return {
    nodes: from.nodes.map((p, i) => point(p, to.nodes[i])),
    nodeOpacity: from.nodeOpacity.map((v, i) => lerp(v, to.nodeOpacity[i])),
    curves: from.curves.map((curve, i) => curve.map((p, j) => point(p, to.curves[i][j])) as CubicPoints),
    edgeOpacity: from.edgeOpacity.map((v, i) => lerp(v, to.edgeOpacity[i])),
  };
}
