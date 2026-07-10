import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import { loadRuntimeConfig } from './amplify-config.js'
import './index.css'
import '@aws-amplify/ui-react/styles.css'

function Boot() {
  const [status, setStatus] = React.useState('loading')
  const [err, setErr] = React.useState(null)

  React.useEffect(() => {
    loadRuntimeConfig()
      .then(() => setStatus('ready'))
      .catch((e) => { setErr(e?.message || String(e)); setStatus('error') })
  }, [])

  if (status === 'loading') {
    return <div style={{padding: 24, fontFamily: 'system-ui', color: '#7a7a7a'}}>Loading…</div>
  }
  if (status === 'error') {
    return (
      <div style={{padding: 24, fontFamily: 'system-ui'}}>
        <div style={{color: '#b91c1c', fontWeight: 600, marginBottom: 8}}>Failed to load workshop config</div>
        <div style={{color: '#7a7a7a', fontSize: 13}}>{err}</div>
      </div>
    )
  }
  return <App />
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <Boot />
  </React.StrictMode>,
)
