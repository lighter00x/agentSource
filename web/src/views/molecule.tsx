import { useEffect, useState } from "react";
import scriptURL from "@rdkit/rdkit?url";
import wasmURL from "@rdkit/rdkit/RDKit_minimal.wasm?url";
type Mol = { get_svg: (w: number, h: number) => string; delete: () => void };
type RDKit = { get_mol: (smiles: string) => Mol | null };
declare global {
  interface Window {
    initRDKitModule?: (options: {
      locateFile: (name: string) => string;
    }) => Promise<RDKit>;
  }
}
let modulePromise: Promise<RDKit> | undefined;
function loadRDKit() {
  if (!modulePromise)
    modulePromise = new Promise<void>((resolve, reject) => {
      if (window.initRDKitModule) {
        resolve();
        return;
      }
      const script = document.createElement("script");
      script.src = scriptURL;
      script.onload = () => resolve();
      script.onerror = () => {
        script.remove();
        reject(new Error("本地 RDKit 资源加载失败"));
      };
      document.head.append(script);
    })
      .then(() => {
        if (!window.initRDKitModule) throw new Error("RDKit 初始化入口缺失");
        return window.initRDKitModule({ locateFile: () => wasmURL });
      })
      .catch((e) => {
        modulePromise = undefined;
        throw e;
      });
  return modulePromise;
}
export function Molecule({
  smiles,
  fallback,
}: {
  smiles: string;
  fallback?: string;
}) {
  const [image, setImage] = useState(""),
    [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    setImage("");
    setError("");
    if (!smiles) return;
    void loadRDKit()
      .then((rdkit) => {
        let mol: Mol | null = null;
        try {
          mol = rdkit.get_mol(smiles);
          if (!mol)
            throw new Error(
              "该结构暂不能绘制；请核对原始 SMILES / Markush 标记",
            );
          const svg = mol.get_svg(320, 200);
          if (active)
            setImage(
              "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg),
            );
        } finally {
          mol?.delete();
        }
      })
      .catch((e) => {
        if (active) setError(String(e));
      });
    return () => {
      active = false;
    };
  }, [smiles]);
  return (
    <div className="molecule">
      {image ? (
        <img alt="化合物结构图" src={image} />
      ) : fallback && (error || !smiles) ? (
        <>
          <img alt="分子原始截图" src={fallback} />
          <p className="muted">{error || "暂无有效结构，显示原图截图"}</p>
        </>
      ) : error ? (
        <p role="alert" className="error">
          {error}
        </p>
      ) : (
        <p className="muted">正在绘制结构…</p>
      )}
    </div>
  );
}
