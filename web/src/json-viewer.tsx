import { useEffect, useMemo, useRef, useState } from "react";
import { Compartment, EditorState } from "@codemirror/state";
import {
  EditorView,
  drawSelection,
  highlightActiveLine,
  highlightActiveLineGutter,
  keymap,
  lineNumbers,
} from "@codemirror/view";
import { json } from "@codemirror/lang-json";
import { defaultKeymap } from "@codemirror/commands";
import {
  bracketMatching,
  HighlightStyle,
  foldAll,
  foldGutter,
  foldKeymap,
  syntaxHighlighting,
  unfoldAll,
} from "@codemirror/language";
import { openSearchPanel, search, searchKeymap } from "@codemirror/search";
import { exportJSON } from "./debug.mjs";
import { tags } from "@lezer/highlight";

const jsonHighlight = HighlightStyle.define([
  { tag: tags.propertyName, class: "json-code-property" },
  { tag: tags.string, class: "json-code-string" },
  { tag: tags.number, class: "json-code-number" },
  { tag: [tags.bool, tags.null], class: "json-code-literal" },
  { tag: tags.punctuation, class: "json-code-punctuation" },
]);
const theme = EditorView.theme({
  "&": { height: "100%", fontSize: "12px", backgroundColor: "#fbfcfe" },
  ".cm-scroller": {
    overflow: "auto",
    fontFamily: "ui-monospace, SFMono-Regular, Consolas, monospace",
    lineHeight: "1.7",
  },
  ".cm-content": { padding: "8px 0" },
  ".cm-gutters": {
    backgroundColor: "#f1f5f9",
    color: "#8391a2",
    borderRight: "1px solid #dde5ed",
  },
  ".cm-activeLine, .cm-activeLineGutter": { backgroundColor: "#eaf2fa" },
  ".cm-searchMatch": { backgroundColor: "#fff0aa" },
  ".cm-searchMatch-selected": { backgroundColor: "#ffd36a" },
  ".cm-selectionBackground, &.cm-focused .cm-selectionBackground": {
    backgroundColor: "#cce1ff",
  },
  ".cm-foldPlaceholder": {
    backgroundColor: "#e8eef5",
    border: "1px solid #d4deea",
    color: "#536a85",
  },
  ".cm-panels": { backgroundColor: "#f1f5f9" },
});

export function JsonViewer({ value }: { value: unknown }) {
  const serialized = useMemo(() => exportJSON(value) ?? "", [value]);
  const available = value !== undefined;
  const host = useRef<HTMLDivElement>(null);
  const editor = useRef<EditorView | null>(null);
  const wrapping = useRef(new Compartment());
  const [wrap, setWrap] = useState(false),
    [copy, setCopy] = useState("");

  useEffect(() => {
    if (!host.current || !available) return;
    const view = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: serialized,
        extensions: [
          json(),
          lineNumbers(),
          foldGutter(),
          bracketMatching(),
          drawSelection(),
          highlightActiveLine(),
          highlightActiveLineGutter(),
          syntaxHighlighting(jsonHighlight),
          search({ top: true }),
          keymap.of([...searchKeymap, ...foldKeymap, ...defaultKeymap]),
          EditorState.readOnly.of(true),
          EditorView.editable.of(false),
          EditorView.contentAttributes.of({
            "aria-label": "JSON 代码查看器",
            tabindex: "0",
          }),
          EditorState.phrases.of({
            Find: "查找",
            next: "下一个",
            previous: "上一个",
            all: "全部",
            "match case": "区分大小写",
            regexp: "正则表达式",
            "by word": "全词匹配",
            close: "关闭",
            "Folded lines": "已折叠代码",
          }),
          wrapping.current.of(wrap ? EditorView.lineWrapping : []),
          theme,
        ],
      }),
    });
    editor.current = view;
    return () => {
      view.destroy();
      editor.current = null;
    };
    // The editor is updated below without recreating its selection/search/scroll state on polling.
  }, [available]);

  useEffect(() => {
    const view = editor.current;
    if (view && view.state.doc.toString() !== serialized) {
      view.dispatch({
        changes: { from: 0, to: view.state.doc.length, insert: serialized },
      });
      setCopy("");
    }
  }, [serialized, available]);
  useEffect(() => {
    editor.current?.dispatch({
      effects: wrapping.current.reconfigure(
        wrap ? EditorView.lineWrapping : [],
      ),
    });
  }, [wrap, available]);

  async function copyJSON() {
    try {
      await navigator.clipboard.writeText(serialized);
      setCopy("已复制完整 JSON");
    } catch {
      const input = document.createElement("textarea");
      input.value = serialized;
      input.style.position = "fixed";
      input.style.opacity = "0";
      document.body.append(input);
      input.select();
      const copied = document.execCommand("copy");
      input.remove();
      if (!copied && editor.current)
        editor.current.dispatch({
          selection: { anchor: 0, head: editor.current.state.doc.length },
        });
      editor.current?.focus();
      setCopy(
        copied ? "已复制完整 JSON" : "已选中全部 JSON，请按 Ctrl/Cmd+C 复制",
      );
    }
  }

  if (!available) return <p className="muted">暂无数据</p>;
  return (
    <div className="json-viewer">
      <div className="json-toolbar">
        <button onClick={() => editor.current && unfoldAll(editor.current)}>
          展开代码
        </button>
        <button onClick={() => editor.current && foldAll(editor.current)}>
          折叠代码
        </button>
        <button
          onClick={() => editor.current && openSearchPanel(editor.current)}
        >
          查找
        </button>
        <button aria-pressed={wrap} onClick={() => setWrap(!wrap)}>
          自动换行
        </button>
        <button onClick={() => void copyJSON()}>复制 JSON</button>
        <small className="muted">
          JSON · {serialized.split("\n").length} 行 · 只读
        </small>
        {copy && <small role="status">{copy}</small>}
      </div>
      <div
        ref={host}
        className="json-code-editor"
        style={{
          height: Math.min(
            480,
            Math.max(100, serialized.split("\n").length * 21 + 20),
          ),
        }}
      />
    </div>
  );
}
