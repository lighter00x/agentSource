import { useEffect, useState } from "react";

export function BBoxCrop({
  sourceRef,
  bbox,
  label,
}: {
  sourceRef: string;
  bbox: unknown;
  label: string;
}) {
  const b = (bbox || {}) as Record<string, number>;
  const { x0, y0, x1, y1 } = b;
  const key = `${sourceRef}:${x0}:${y0}:${x1}:${y1}`;
  const [result, setResult] = useState<{
    key: string;
    url?: string;
    error?: string;
  }>();
  useEffect(() => {
    let active = true;
    const image = new Image();
    image.onload = () => {
      if (!active) return;
      try {
        if (![x0, y0, x1, y1].every(Number.isFinite) || x1 <= x0 || y1 <= y0)
          throw new Error("检测 bbox 无效，无法裁剪。");
        // Match the backend's outward rounding without extending the bbox.
        const left = Math.max(0, Math.floor(x0 * image.naturalWidth + 1e-9));
        const top = Math.max(0, Math.floor(y0 * image.naturalHeight + 1e-9));
        const right = Math.min(
          image.naturalWidth,
          Math.ceil(x1 * image.naturalWidth - 1e-9),
        );
        const bottom = Math.min(
          image.naturalHeight,
          Math.ceil(y1 * image.naturalHeight - 1e-9),
        );
        if (right <= left || bottom <= top)
          throw new Error("检测 bbox 超出原图范围，无法裁剪。");
        const canvas = document.createElement("canvas");
        canvas.width = right - left;
        canvas.height = bottom - top;
        const ctx = canvas.getContext("2d");
        if (!ctx) throw new Error("浏览器无法生成裁剪预览。");
        ctx.drawImage(
          image,
          left,
          top,
          canvas.width,
          canvas.height,
          0,
          0,
          canvas.width,
          canvas.height,
        );
        setResult({ key, url: canvas.toDataURL("image/png") });
      } catch (e) {
        setResult({ key, error: String(e) });
      }
    };
    image.onerror = () => {
      if (active) setResult({ key, error: "检测原图加载失败，无法裁剪。" });
    };
    image.src = `/api/v1/blobs/${encodeURIComponent(sourceRef)}`;
    return () => {
      active = false;
      image.onload = null;
      image.onerror = null;
    };
  }, [sourceRef, x0, y0, x1, y1, key]);
  const current = result?.key === key ? result : undefined;
  return current?.url ? (
    <img src={current.url} alt={label} />
  ) : (
    <p className="muted">{current?.error || "正在按 bbox 裁剪…"}</p>
  );
}
