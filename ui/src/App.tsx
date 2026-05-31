import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import './App.css'
import { inspectCsvs } from './api'

function App() {
  const [files, setFiles] = useState<File[]>([])
  const [fk, setFk] = useState('')
  const [markdown, setMarkdown] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function onInspect() {
    setError('')
    setMarkdown('')
    setLoading(true)
    try {
      const fks = fk
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean)
      setMarkdown(await inspectCsvs(files, fks))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="container">
      <h1>csv2rdf-mcp — Step 0 Inspector</h1>
      <p className="subtitle">
        CSV をアップロードすると、型 / JSON 列 / 一意性 (複合キー) の構造解析を表示します。
        LLM は使いません。
      </p>

      <section className="controls">
        <input
          type="file"
          accept=".csv"
          multiple
          onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
        />
        <label>
          FK 列ヒント (カンマ区切り・任意)
          <input
            type="text"
            value={fk}
            placeholder="SID"
            onChange={(e) => setFk(e.target.value)}
          />
        </label>
        <button onClick={onInspect} disabled={loading || files.length === 0}>
          {loading ? 'Inspecting…' : 'Inspect'}
        </button>
        {files.length > 0 && (
          <span className="hint">{files.length} file(s) selected</span>
        )}
      </section>

      {error && <pre className="error">{error}</pre>}
      {markdown && (
        <section className="result">
          <ReactMarkdown>{markdown}</ReactMarkdown>
        </section>
      )}
    </main>
  )
}

export default App
