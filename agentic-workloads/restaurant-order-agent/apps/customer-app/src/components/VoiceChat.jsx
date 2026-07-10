import { useState, useRef, useEffect } from 'react'
import { Mic, MicOff, MessageCircle, X, Send, Phone, Volume2 } from 'lucide-react'

const API_BASE = import.meta.env.VITE_API_BASE || '/api'

/**
 * VoiceChat — simple and reliable.
 * 
 * Uses Web Speech API in single-shot mode (non-continuous) for STT.
 * Uses fetch /api/chat for conversation (Claude with memory).
 * Uses browser SpeechSynthesis for TTS.
 * 
 * Flow: User taps mic once → enters voice mode → cycle:
 *   listen (single shot) → send → TTS response → auto-restart listen
 * 
 * No continuous mode, no Deepgram direct connection needed.
 * The endpointing issue is solved by letting SpeechRecognition handle it
 * naturally (single-shot mode stops when user pauses naturally).
 */
export default function VoiceChat() {
  const [open, setOpen] = useState(false)
  const [messages, setMessages] = useState([
    { role: 'assistant', text: "Hi! I'm your Tasty Bites voice assistant. Tap the mic to start." }
  ])
  const [input, setInput] = useState('')
  const [active, setActive] = useState(false)
  const [phase, setPhase] = useState('idle') // idle | listening | processing | speaking
  const messagesEnd = useRef(null)
  const sessionIdRef = useRef(null)
  const voiceRef = useRef(null)
  const activeRef = useRef(false)
  const recognitionRef = useRef(null)

  useEffect(() => { messagesEnd.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])
  useEffect(() => { activeRef.current = active }, [active])

  // TTS voice
  useEffect(() => {
    const pick = () => {
      const voices = window.speechSynthesis?.getVoices() || []
      voiceRef.current = voices.find(v => v.name === 'Google UK English Female') ||
        voices.find(v => v.name.includes('Samantha')) ||
        voices.find(v => v.name.includes('Google') && v.lang.startsWith('en')) ||
        voices.find(v => v.lang.startsWith('en'))
    }
    pick()
    window.speechSynthesis?.addEventListener('voiceschanged', pick)
    return () => window.speechSynthesis?.removeEventListener('voiceschanged', pick)
  }, [])

  // --- Single-shot listen → process → speak → repeat ---

  function doListen() {
    if (!activeRef.current) return
    if (!('webkitSpeechRecognition' in window || 'SpeechRecognition' in window)) return

    const SR = window.SpeechRecognition || window.webkitSpeechRecognition
    const rec = new SR()
    rec.continuous = false
    rec.interimResults = false
    rec.lang = 'en-IN'

    rec.onstart = () => setPhase('listening')
    rec.onresult = (e) => {
      const text = e.results[0]?.[0]?.transcript?.trim()
      if (text) doProcess(text)
      else doListen() // empty result, retry
    }
    rec.onerror = (e) => {
      if (e.error === 'no-speech' || e.error === 'aborted') {
        // Retry after brief pause
        if (activeRef.current) setTimeout(doListen, 200)
        else setPhase('idle')
      } else if (e.error === 'not-allowed') {
        setMessages(prev => [...prev, { role: 'assistant', text: '🎤 Mic blocked. Allow in browser settings.' }])
        setActive(false)
        setPhase('idle')
      } else {
        if (activeRef.current) setTimeout(doListen, 500)
        else setPhase('idle')
      }
    }
    rec.onend = () => {
      recognitionRef.current = null
      // If no result/error fired (edge case), restart
      if (activeRef.current && phase === 'listening') {
        setTimeout(doListen, 200)
      }
    }

    recognitionRef.current = rec
    try { rec.start() } catch { setTimeout(doListen, 300) }
  }

  async function doProcess(text) {
    setPhase('processing')
    setMessages(prev => [...prev, { role: 'user', text }])

    try {
      const token = localStorage.getItem('auth_token')
      const res = await fetch(`${API_BASE}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(token ? { 'Authorization': `Bearer ${token}` } : {}) },
        body: JSON.stringify({ message: text, session_id: sessionIdRef.current }),
      })
      const data = await res.json()
      const response = data.response || "Sorry, I didn't catch that."
      if (data.session_id) sessionIdRef.current = data.session_id
      setMessages(prev => [...prev, { role: 'assistant', text: response }])
      doSpeak(response)
    } catch {
      setMessages(prev => [...prev, { role: 'assistant', text: 'Connection error.' }])
      if (activeRef.current) setTimeout(doListen, 500)
      else setPhase('idle')
    }
  }

  function doSpeak(text) {
    if (!window.speechSynthesis || !text || !activeRef.current) {
      if (activeRef.current) doListen()
      else setPhase('idle')
      return
    }

    setPhase('speaking')
    window.speechSynthesis.cancel()

    const clean = text.replace(/\*\*/g, '').replace(/\*/g, '').replace(/#{1,6}\s/g, '')
      .replace(/- /g, ', ').replace(/\n+/g, '. ').replace(/₹(\d+)/g, 'rupees $1').trim()

    const utt = new SpeechSynthesisUtterance(clean)
    utt.rate = 1.0
    utt.pitch = 1.0
    utt.lang = 'en-IN'
    if (voiceRef.current) utt.voice = voiceRef.current

    utt.onend = () => {
      setPhase('idle')
      if (activeRef.current) setTimeout(doListen, 150)
    }
    utt.onerror = () => {
      setPhase('idle')
      if (activeRef.current) setTimeout(doListen, 150)
    }

    window.speechSynthesis.speak(utt)
  }

  // --- Toggle ---

  function toggleMic() {
    if (active) {
      setActive(false)
      activeRef.current = false
      if (recognitionRef.current) { recognitionRef.current.abort(); recognitionRef.current = null }
      window.speechSynthesis?.cancel()
      setPhase('idle')
    } else {
      setActive(true)
      activeRef.current = true
      doListen()
    }
  }

  // --- Text send ---
  async function handleSend(text) {
    const msg = text || input
    if (!msg.trim()) return
    setInput('')
    setMessages(prev => [...prev, { role: 'user', text: msg }])
    setPhase('processing')
    try {
      const token = localStorage.getItem('auth_token')
      const res = await fetch(`${API_BASE}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(token ? { 'Authorization': `Bearer ${token}` } : {}) },
        body: JSON.stringify({ message: msg, session_id: sessionIdRef.current }),
      })
      const data = await res.json()
      if (data.session_id) sessionIdRef.current = data.session_id
      setMessages(prev => [...prev, { role: 'assistant', text: data.response || 'Error' }])
    } catch {
      setMessages(prev => [...prev, { role: 'assistant', text: 'Connection error.' }])
    }
    setPhase('idle')
  }

  useEffect(() => () => { window.speechSynthesis?.cancel() }, [])

  const statusText = { idle: 'Tap mic to start', listening: '🎤 Listening...', processing: '⏳ Thinking...', speaking: '🔊 Speaking...' }

  return (
    <>
      <button onClick={() => setOpen(!open)} className="fixed bottom-6 right-6 w-14 h-14 bg-orange-500 text-white rounded-full shadow-lg hover:bg-orange-600 transition flex items-center justify-center z-50">
        {open ? <X className="w-6 h-6" /> : <MessageCircle className="w-6 h-6" />}
      </button>

      {open && (
        <div className="fixed bottom-24 right-6 w-96 h-[520px] bg-white rounded-2xl shadow-2xl border flex flex-col z-50 overflow-hidden">
          <div className="bg-orange-500 text-white px-4 py-3 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Phone className="w-5 h-5" />
              <span className="font-semibold text-sm">Voice Assistant</span>
            </div>
            <div className="flex items-center gap-1.5">
              {phase === 'speaking' && <Volume2 className="w-4 h-4 animate-pulse" />}
              {phase === 'listening' && <span className="w-2 h-2 bg-green-300 rounded-full animate-pulse" />}
              <span className="text-xs text-orange-100">{statusText[phase]}</span>
            </div>
          </div>

          <div className="flex-1 overflow-y-auto p-4 space-y-3">
            {messages.map((msg, i) => (
              <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                <div className={`max-w-[80%] px-4 py-2 rounded-2xl text-sm whitespace-pre-wrap ${msg.role === 'user' ? 'bg-orange-500 text-white rounded-br-md' : 'bg-gray-100 text-gray-800 rounded-bl-md'}`}>{msg.text}</div>
              </div>
            ))}
            <div ref={messagesEnd} />
          </div>

          <div className="border-t p-3 flex items-center gap-2">
            <button onClick={toggleMic} className={`p-3 rounded-full transition-all ${active ? 'bg-red-500 text-white shadow-lg shadow-red-200 animate-pulse' : 'bg-gray-100 text-gray-600 hover:bg-orange-100 hover:text-orange-600'}`}>
              {active ? <MicOff className="w-5 h-5" /> : <Mic className="w-5 h-5" />}
            </button>
            <input type="text" value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && handleSend()} placeholder={active ? 'Voice active' : 'Type here...'} className="flex-1 px-3 py-2 border border-gray-200 rounded-xl text-sm focus:ring-2 focus:ring-orange-500 outline-none" disabled={phase === 'processing'} />
            <button onClick={() => handleSend()} disabled={!input.trim() || phase === 'processing'} className="p-2 bg-orange-500 text-white rounded-full hover:bg-orange-600 disabled:opacity-50 transition">
              <Send className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}
    </>
  )
}
