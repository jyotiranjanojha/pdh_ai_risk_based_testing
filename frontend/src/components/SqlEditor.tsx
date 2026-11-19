"use client";

import React from "react";
import CodeMirror from "@uiw/react-codemirror";
import { sql } from "@codemirror/lang-sql";
import { oneDark } from "@codemirror/theme-one-dark";
import { useAppContext } from "./AppContext";

interface SqlEditorProps {
  value: string;
  onChange?: (val: string) => void;
  readOnly?: boolean;
  height?: string;
}

export function SqlEditor({
  value,
  onChange,
  readOnly = false,
  height = "220px",
}: SqlEditorProps) {
  const { darkMode } = useAppContext();

  return (
    <div className="overflow-hidden rounded-lg border border-slate-300 dark:border-slate-700">
      <CodeMirror
        value={value}
        height={height}
        theme={darkMode ? oneDark : "light"}
        extensions={[sql()]}
        readOnly={readOnly}
        onChange={(val) => onChange?.(val)}
        basicSetup={{
          lineNumbers: true,
          foldGutter: true,
          highlightActiveLine: !readOnly,
        }}
      />
    </div>
  );
}
